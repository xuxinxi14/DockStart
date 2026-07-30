from __future__ import annotations

import hashlib
import itertools
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import ad4zn as ad4zn_module  # noqa: E402
from dockstart_core.ad4zn import (  # noqa: E402
    REQUIRED_CONFIRMATIONS,
    _tz_direction_from_coordination_plane,
    get_status,
    prepare_receptor,
    record_parameter_file,
    save_review,
    validate_parameter_file,
)
from dockstart_core.project import (  # noqa: E402
    create_project,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    load_project,
)


def _atom_line(
    serial: int,
    name: str,
    residue_name: str,
    chain: str,
    residue_number: int,
    x: float,
    y: float,
    z: float,
    charge: float,
    atom_type: str,
    *,
    record_type: str = "ATOM",
) -> str:
    return (
        f"{record_type:<6}{serial:5d} {name:^4} {residue_name:>3} "
        f"{chain:1}{residue_number:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}"
        f"{1.00:6.2f}{0.00:6.2f}    "
        f"{charge:6.3f} {atom_type:>2}\n"
    )


def _three_coordinate_site(
    *,
    serial_offset: int = 0,
    x_offset: float = 0.0,
    zinc_charge: float = 1.25,
    zinc_type: str = "Zn",
    carboxylate_pair: bool = False,
) -> list[str]:
    lines = [
        _atom_line(
            1 + serial_offset,
            "ZN",
            "ZN",
            "A",
            500 + serial_offset,
            x_offset,
            0.0,
            0.0,
            zinc_charge,
            zinc_type,
            record_type="HETATM",
        )
    ]
    if carboxylate_pair:
        lines.extend(
            [
                _atom_line(
                    2 + serial_offset,
                    "OD1",
                    "ASP",
                    "A",
                    10 + serial_offset,
                    x_offset + 1.8,
                    0.9,
                    0.0,
                    -0.5,
                    "OA",
                ),
                _atom_line(
                    3 + serial_offset,
                    "OD2",
                    "ASP",
                    "A",
                    10 + serial_offset,
                    x_offset + 1.8,
                    -0.9,
                    0.0,
                    -0.5,
                    "OA",
                ),
                _atom_line(
                    4 + serial_offset,
                    "CG",
                    "ASP",
                    "A",
                    10 + serial_offset,
                    x_offset + 2.7,
                    0.0,
                    0.0,
                    0.3,
                    "C",
                ),
                _atom_line(
                    5 + serial_offset,
                    "NE2",
                    "HIS",
                    "A",
                    11 + serial_offset,
                    x_offset,
                    2.0,
                    0.0,
                    -0.2,
                    "NA",
                ),
                _atom_line(
                    6 + serial_offset,
                    "SG",
                    "CYS",
                    "A",
                    12 + serial_offset,
                    x_offset,
                    0.0,
                    2.0,
                    -0.2,
                    "SA",
                ),
            ]
        )
    else:
        lines.extend(
            [
                _atom_line(
                    2 + serial_offset,
                    "NE2",
                    "HIS",
                    "A",
                    10 + serial_offset,
                    x_offset + 2.0,
                    0.0,
                    0.0,
                    -0.2,
                    "NA",
                ),
                _atom_line(
                    3 + serial_offset,
                    "OD1",
                    "ASN",
                    "A",
                    11 + serial_offset,
                    x_offset,
                    2.0,
                    0.0,
                    -0.4,
                    "OA",
                ),
                _atom_line(
                    4 + serial_offset,
                    "SG",
                    "CYS",
                    "A",
                    12 + serial_offset,
                    x_offset,
                    0.0,
                    2.0,
                    -0.2,
                    "SA",
                ),
            ]
        )
    return lines


def _valid_parameter_text() -> str:
    return "\n".join(
        [
            "# user supplied test fixture, not an AD4Zn distribution asset",
            "# GNU General Public License, version 2 or later",
            "FE_coeff_vdW 0.1662",
            "FE_coeff_hbond 0.1209",
            "FE_coeff_estat 0.1406",
            "FE_coeff_desolv 0.1322",
            "FE_coeff_tors 0.2983",
            "atom_par C 4.00 0.150 33.5103 -0.00143 0.0 0.0 0 -1 -1 0",
            "atom_par N 3.50 0.160 22.4493 -0.00162 0.0 0.0 0 -1 -1 1",
            "atom_par NA 3.50 0.160 22.4493 -0.00162 1.9 5.0 4 -1 -1 1",
            "atom_par OA 3.20 0.200 17.1573 -0.00251 1.9 5.0 5 -1 -1 2",
            "atom_par SA 4.00 0.200 33.5103 -0.00214 2.5 1.0 5 -1 -1 6",
            "atom_par Z 4.00 0.150 33.5103 -0.00143 0.0 0.0 0 -1 -1 0",
            "atom_par ZN 1.48 0.550 1.7000 -0.00110 0.0 0.0 0 -1 -1 4",
            "atom_par TZ 1.00 0.000 0.0000 0.00000 0.0 0.0 0 -1 -1 0",
            "",
        ]
    )


class AD4ZnTests(unittest.TestCase):
    def setUp(self) -> None:
        reference_sha256 = hashlib.sha256(
            _valid_parameter_text().encode("utf-8")
        ).hexdigest()
        patcher = patch.object(
            ad4zn_module,
            "SUPPORTED_PARAMETER_REFERENCE_SHA256",
            reference_sha256,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create_project(self, root: str, receptor_lines: list[str]) -> Path:
        response = create_project("ad4zn_project", root)
        self.assertTrue(response["ok"], response)
        project_dir = Path(response["project_dir"])
        source = Path(root) / "source_receptor.pdbqt"
        source.write_text("REMARK untouched record\n" + "".join(receptor_lines), encoding="utf-8")
        imported = import_receptor_pdbqt(str(project_dir), str(source))
        self.assertTrue(imported["ok"], imported)
        return project_dir

    def _review_payload(self, status: dict, index: int = 0) -> dict:
        return {
            "selected_site_id": status["sites"][index]["site_id"],
            "confirmations": {key: True for key in REQUIRED_CONFIRMATIONS},
        }

    def _save_valid_review(self, project_dir: Path, index: int = 0) -> dict:
        status = get_status(str(project_dir))
        response = save_review(
            str(project_dir),
            self._review_payload(status, index),
        )
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["review_valid"], response)
        return response

    def _record_valid_parameter(self, project_dir: Path, root: str) -> dict:
        source = Path(root) / "user_AD4Zn.dat"
        source.write_text(_valid_parameter_text(), encoding="utf-8")
        response = record_parameter_file(str(project_dir), source)
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["parameter_file_valid"], response)
        return response

    def test_status_without_zinc_has_structured_issue(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                [
                    _atom_line(1, "CA", "ALA", "A", 1, 0, 0, 0, 0.1, "C"),
                    _atom_line(2, "O", "ALA", "A", 1, 1, 0, 0, -0.2, "OA"),
                ],
            )
            status = get_status(str(project_dir))
            self.assertTrue(status["ok"])
            self.assertFalse(status["preparation_ready"])
            self.assertEqual(status["sites"], [])
            issue = next(item for item in status["issues"] if item["code"] == "AD4ZN_ZN_NOT_FOUND")
            self.assertTrue(issue["blocking"])
            self.assertIn("锌", issue["title"])

    def test_non_zinc_metal_is_reported_and_blocks_review(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            lines = _three_coordinate_site()
            lines.append(
                _atom_line(
                    20,
                    "FE",
                    "HEM",
                    "B",
                    600,
                    10,
                    10,
                    10,
                    2.0,
                    "Fe",
                    record_type="HETATM",
                )
            )
            project_dir = self._create_project(root, lines)
            status = get_status(str(project_dir))
            self.assertEqual(len(status["other_metals"]), 1)
            self.assertTrue(
                any(
                    item["code"] == "AD4ZN_NON_ZN_METAL_UNSUPPORTED"
                    for item in status["issues"]
                )
            )
            review = save_review(str(project_dir), self._review_payload(status))
            self.assertFalse(review["ok"])
            self.assertEqual(
                review["error"]["code"],
                "AD4ZN_NON_ZN_METAL_UNSUPPORTED",
            )

    def test_single_zinc_site_and_official_distance_limits(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            status = get_status(str(project_dir))
            self.assertEqual(status["protocol_id"], "ad4zn_beta")
            self.assertEqual(len(status["sites"]), 1)
            site = status["sites"][0]
            self.assertEqual(site["coordination_number"], 3)
            self.assertTrue(site["can_generate"])
            self.assertEqual(site["limits"]["neighbor_search_angstrom"], 4.5)
            self.assertEqual(site["limits"]["coordination_cutoff_angstrom"], 2.5)
            self.assertEqual(site["tz_candidate"]["distance"], 2.0)
            self.assertAlmostEqual(
                math.sqrt(
                    sum(
                        value * value
                        for value in site["tz_candidate"]["direction"].values()
                    )
                ),
                1.0,
                places=7,
            )

    def test_carboxylate_double_oxygen_counts_as_one_group(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                _three_coordinate_site(carboxylate_pair=True),
            )
            site = get_status(str(project_dir))["sites"][0]
            self.assertEqual(site["coordination_number"], 3)
            carboxylate = next(
                group
                for group in site["coordination_groups"]
                if group["kind"] == "carboxylate"
            )
            self.assertEqual(len(carboxylate["members"]), 2)
            self.assertTrue(site["can_generate"])

    def test_carboxylate_uses_official_distance_weighted_coordinate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                [
                    _atom_line(
                        1,
                        "ZN",
                        "ZN",
                        "A",
                        500,
                        0,
                        0,
                        0,
                        1.0,
                        "Zn",
                        record_type="HETATM",
                    ),
                    _atom_line(2, "CG", "ASP", "A", 10, 2.5, 0.2, 0, 0.2, "C"),
                    _atom_line(3, "OD1", "ASP", "A", 10, 1.4, 0.9, 0, -0.5, "OA"),
                    _atom_line(4, "OD2", "ASP", "A", 10, 2.0, -1.0, 0, -0.5, "OA"),
                    _atom_line(5, "NE2", "HIS", "A", 11, 0, 2, 0, -0.2, "NA"),
                    _atom_line(6, "SG", "CYS", "A", 12, 0, 0, 2, -0.2, "SA"),
                ],
            )
            site = get_status(str(project_dir))["sites"][0]
            carboxylate = next(
                group
                for group in site["coordination_groups"]
                if group["kind"] == "carboxylate"
            )
            d1 = math.dist((1.4, 0.9, 0.0), (0.0, 0.0, 0.0))
            d2 = math.dist((2.0, -1.0, 0.0), (0.0, 0.0, 0.0))
            oo = math.dist((1.4, 0.9, 0.0), (2.0, -1.0, 0.0))
            weight = (1.0 - ((d2 - d1) / oo) ** 0.5) / 2.0
            coordinate = carboxylate["representative_coordinate"]
            self.assertAlmostEqual(coordinate["x"], 1.4 + 0.6 * weight)
            self.assertAlmostEqual(coordinate["y"], 0.9 - 1.9 * weight)
            self.assertNotAlmostEqual(coordinate["x"], 1.7)
            self.assertEqual(carboxylate["averaging_exponent"], 0.5)

    def test_one_two_and_one_three_connected_atoms_are_not_double_counted(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                [
                    _atom_line(
                        1,
                        "ZN",
                        "ZN",
                        "A",
                        500,
                        0,
                        0,
                        0,
                        1.0,
                        "Zn",
                        record_type="HETATM",
                    ),
                    _atom_line(2, "N1", "LIG", "A", 10, 1.5, 0, 0, -0.2, "NA"),
                    _atom_line(3, "C1", "LIG", "A", 10, 1.5, 1.2, 0, 0.2, "C"),
                    _atom_line(4, "O1", "LIG", "A", 10, 0.8, 1.9, 0, -0.5, "OA"),
                    _atom_line(5, "N2", "HIS", "A", 11, -1.7, -1.2, 0, -0.2, "NA"),
                    _atom_line(6, "SG", "CYS", "A", 12, 0, 0, 2, -0.2, "SA"),
                ],
            )
            site = get_status(str(project_dir))["sites"][0]
            self.assertEqual(site["coordination_number"], 3)
            oxygen = next(
                row for row in site["neighbors"] if row["atom"]["name"] == "O1"
            )
            self.assertFalse(oxygen["coordinating"])
            self.assertTrue(oxygen["excluded_by_connectivity"])
            self.assertTrue(site["can_generate"])

    def test_z_connector_participates_in_official_one_three_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                [
                    _atom_line(
                        1,
                        "ZN",
                        "ZN",
                        "A",
                        500,
                        0,
                        0,
                        0,
                        1.0,
                        "Zn",
                        record_type="HETATM",
                    ),
                    _atom_line(2, "Z1", "LIG", "A", 10, 0.9, 1.1, 0, 0.0, "Z"),
                    _atom_line(3, "N1", "LIG", "A", 10, 1.8, 0, 0, -0.2, "NA"),
                    _atom_line(4, "O1", "LIG", "A", 10, 0, 2.2, 0, -0.5, "OA"),
                    _atom_line(5, "S1", "CYS", "A", 11, -2.3, 0, 0.4, -0.2, "SA"),
                    _atom_line(6, "O2", "ASP", "A", 12, 0, -2.3, 0.6, -0.5, "OA"),
                ],
            )
            site = get_status(str(project_dir))["sites"][0]
            self.assertEqual(site["coordination_number"], 3)
            excluded = next(
                row for row in site["neighbors"] if row["atom"]["name"] == "O1"
            )
            self.assertTrue(excluded["excluded_by_connectivity"])
            self.assertFalse(excluded["coordinating"])
            self.assertTrue(site["can_generate"])

    def test_nearly_coplanar_zinc_site_requires_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                [
                    _atom_line(
                        1,
                        "ZN",
                        "ZN",
                        "A",
                        500,
                        0,
                        0,
                        0,
                        1.0,
                        "Zn",
                        record_type="HETATM",
                    ),
                    _atom_line(2, "O1", "ASP", "A", 10, 0.449, 0.208, 1.422, -0.5, "OA"),
                    _atom_line(3, "O2", "ASN", "A", 11, -0.341, 0.137, -1.480, -0.4, "OA"),
                    _atom_line(4, "S1", "CYS", "A", 12, 0.512, 1.965, -0.870, -0.2, "SA"),
                ],
            )
            site = get_status(str(project_dir))["sites"][0]
            self.assertEqual(site["coordination_number"], 3)
            self.assertFalse(site["can_generate"])
            self.assertIsNone(site["tz_candidate"])
            self.assertLess(site["plane_separation_degrees"], 1.0)
            self.assertIn("近乎共面", site["geometry_message"])
            status = get_status(str(project_dir))
            self.assertFalse(status["step_readiness"]["review"])
            rejected = save_review(
                str(project_dir),
                self._review_payload(status),
            )
            self.assertFalse(rejected["ok"])
            self.assertEqual(
                rejected["error"]["code"],
                "AD4ZN_TZ_GEOMETRY_UNSUPPORTED",
            )

    def test_nearby_second_zinc_blocks_unsupported_multinuclear_site(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                [
                    _atom_line(
                        1,
                        "ZN1",
                        "ZN",
                        "A",
                        500,
                        0,
                        0,
                        0,
                        1.0,
                        "Zn",
                        record_type="HETATM",
                    ),
                    _atom_line(
                        2,
                        "ZN2",
                        "ZN",
                        "A",
                        501,
                        2,
                        0,
                        0,
                        1.0,
                        "Zn",
                        record_type="HETATM",
                    ),
                    _atom_line(3, "N1", "HIS", "A", 10, 0, 2, 0, -0.2, "NA"),
                    _atom_line(4, "O1", "ASN", "A", 11, 0, 0, 2, -0.4, "OA"),
                ],
            )
            sites = get_status(str(project_dir))["sites"]
            first_site = next(site for site in sites if site["zn"]["serial"] == 1)
            self.assertFalse(first_site["can_generate"])
            self.assertEqual(len(first_site["nearby_metals"]), 1)
            self.assertEqual(
                first_site["nearby_metals"][0]["atom"]["serial"],
                2,
            )
            self.assertIn("多核金属位点", first_site["geometry_message"])
            rejected = save_review(
                str(project_dir),
                self._review_payload({"sites": sites}),
            )
            self.assertFalse(rejected["ok"])
            self.assertEqual(
                rejected["error"]["code"],
                "AD4ZN_MULTIPLE_ZN_UNSUPPORTED",
            )

    def test_official_1s63_site_reproduces_reference_tz_coordinate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                [
                    _atom_line(
                        7206,
                        "ZN",
                        "ZN",
                        "B",
                        1001,
                        18.142,
                        132.126,
                        5.224,
                        0.0,
                        "Zn",
                        record_type="HETATM",
                    ),
                    _atom_line(6022, "CG", "ASP", "B", 297, 19.396, 130.992, 7.163, 0.292, "C"),
                    _atom_line(6023, "OD1", "ASP", "B", 297, 18.217, 131.149, 7.557, -0.263, "OA"),
                    _atom_line(6024, "OD2", "ASP", "B", 297, 19.835, 131.602, 6.166, -0.150, "OA"),
                    _atom_line(6036, "SG", "CYS", "B", 299, 17.296, 130.098, 4.417, -0.095, "SA"),
                    _atom_line(6645, "NE2", "HIS", "B", 362, 16.818, 133.548, 5.999, -0.254, "N"),
                ],
            )
            site = get_status(str(project_dir))["sites"][0]
            coordinate = site["tz_candidate"]["coordinate"]
            self.assertAlmostEqual(coordinate["x"], 19.112964205248026, places=9)
            self.assertAlmostEqual(coordinate["y"], 132.9641742165903, places=9)
            self.assertAlmostEqual(coordinate["z"], 3.6894992685663848, places=9)

    def test_coordination_plane_direction_is_permutation_invariant(self) -> None:
        zinc = {"coordinate": {"x": 18.142, "y": 132.126, "z": 5.224}}
        representatives = [
            {"x": 16.818, "y": 133.548, "z": 5.999},
            {"x": 17.296, "y": 130.098, "z": 4.417},
            {
                "x": 19.42228188867361,
                "y": 131.48644913199576,
                "z": 6.520815137734863,
            },
        ]
        baseline = _tz_direction_from_coordination_plane(
            zinc,
            representatives,
        )
        self.assertIsNotNone(baseline[0])
        for permutation in itertools.permutations(representatives):
            result = _tz_direction_from_coordination_plane(
                zinc,
                list(permutation),
            )
            self.assertIsNotNone(result[0])
            for actual, expected in zip(result[0], baseline[0], strict=True):
                self.assertAlmostEqual(actual, expected, places=12)
            self.assertAlmostEqual(result[2], baseline[2], places=12)
            self.assertAlmostEqual(result[3], baseline[3], places=12)

    def test_nonstandard_carboxylate_is_inferred_from_shared_carbon(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                [
                    _atom_line(
                        1,
                        "ZN",
                        "ZN",
                        "A",
                        500,
                        0,
                        0,
                        0,
                        1.0,
                        "Zn",
                        record_type="HETATM",
                    ),
                    _atom_line(2, "C1", "LIG", "A", 501, 2.1, 0, 0, 0.2, "C"),
                    _atom_line(3, "O1", "LIG", "A", 501, 1.2, 0.8, 0, -0.5, "OA"),
                    _atom_line(4, "O2", "LIG", "A", 501, 1.2, -0.8, 0, -0.5, "OA"),
                    _atom_line(5, "NE2", "HIS", "A", 10, 0, 2, 0, -0.2, "NA"),
                    _atom_line(6, "SG", "CYS", "A", 11, 0, 0, 2, -0.2, "SA"),
                ],
            )
            site = get_status(str(project_dir))["sites"][0]
            self.assertEqual(site["coordination_number"], 3)
            inferred = next(
                group
                for group in site["coordination_groups"]
                if group["kind"] == "carboxylate"
            )
            self.assertEqual(len(inferred["members"]), 2)
            self.assertTrue(site["can_generate"])

    def test_multiple_zinc_is_rejected_even_with_explicit_selection(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            lines = _three_coordinate_site()
            lines.extend(_three_coordinate_site(serial_offset=20, x_offset=20.0))
            project_dir = self._create_project(root, lines)
            status = get_status(str(project_dir))
            self.assertEqual(len(status["sites"]), 2)
            self.assertFalse(status["step_readiness"]["review"])
            self.assertFalse(status["step_readiness"]["prepare"])
            self.assertTrue(
                any(
                    item["code"] == "AD4ZN_MULTIPLE_ZN_UNSUPPORTED"
                    for item in status["issues"]
                )
            )
            rejected = save_review(
                str(project_dir),
                self._review_payload(status, index=1),
            )
            self.assertFalse(rejected["ok"])
            self.assertEqual(
                rejected["error"]["code"],
                "AD4ZN_MULTIPLE_ZN_UNSUPPORTED",
            )

    def test_all_seven_confirmations_must_be_literal_true(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            status = get_status(str(project_dir))
            payload = self._review_payload(status)
            payload["confirmations"]["water"] = 1
            response = save_review(str(project_dir), json.dumps(payload))
            self.assertFalse(response["ok"])
            self.assertEqual(
                response["error"]["code"],
                "AD4ZN_CONFIRMATIONS_REQUIRED",
            )

    def test_review_is_invalidated_when_current_receptor_hash_changes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            self._save_valid_review(project_dir)
            receptor = project_dir / "prepared" / "receptor.pdbqt"
            receptor.write_text(
                receptor.read_text(encoding="utf-8") + "REMARK changed after review\n",
                encoding="utf-8",
            )
            status = get_status(str(project_dir))
            self.assertFalse(status["review_valid"])
            self.assertTrue(
                any(
                    item["code"] == "AD4ZN_REVIEW_RECEPTOR_CHANGED"
                    for item in status["issues"]
                )
            )

    def test_prepare_writes_exactly_one_zinc_one_tz_and_one_charge_change(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            lines = _three_coordinate_site(zinc_charge=1.5)
            # Two stale pseudoatoms must be removed before one selected TZ is inserted.
            lines.extend(
                [
                    _atom_line(
                        80,
                        "TZ",
                        "ZN",
                        "A",
                        500,
                        -1,
                        -1,
                        -1,
                        0.5,
                        "TZ",
                        record_type="HETATM",
                    ),
                    _atom_line(
                        81,
                        "TZ",
                        "ZN",
                        "A",
                        500,
                        -2,
                        -1,
                        -1,
                        0.5,
                        "TZ",
                        record_type="HETATM",
                    ),
                ]
            )
            project_dir = self._create_project(root, lines)
            self._save_valid_review(project_dir, index=0)
            response = prepare_receptor(str(project_dir))
            self.assertTrue(response["ok"], response)
            prepared = response["prepared_receptor"]
            self.assertTrue(prepared["valid"], prepared)
            self.assertEqual(prepared["removed_existing_tz_count"], 2)
            self.assertEqual(len(prepared["all_zn_charge_changes"]), 1)
            output = project_dir / prepared["relative_path"]
            self.assertTrue(output.is_file())
            output_lines = output.read_text(encoding="utf-8").splitlines()
            atom_lines = [
                line
                for line in output_lines
                if line[:6].strip().upper() in {"ATOM", "HETATM"}
            ]
            tz_lines = [line for line in atom_lines if line[77:79].strip() == "TZ"]
            zn_lines = [line for line in atom_lines if line[77:79].strip() == "ZN"]
            self.assertEqual(len(tz_lines), 1)
            self.assertEqual(len(zn_lines), 1)
            self.assertTrue(all(float(line[68:76]) == 0.0 for line in zn_lines))
            self.assertEqual(float(tz_lines[0][68:76]), 0.0)
            self.assertEqual(tz_lines[0][12:16], "  TZ")
            self.assertAlmostEqual(
                prepared["tz"]["serialized_distance_angstrom"],
                2.0,
                delta=0.001,
            )
            self.assertIn("REMARK untouched record", output.read_text(encoding="utf-8"))

            loaded = load_project(str(project_dir))
            self.assertTrue(loaded["ok"])
            self.assertEqual(
                loaded["project"]["receptor"]["file"],
                "prepared/receptor.pdbqt",
            )
            self.assertNotEqual(
                loaded["project"]["receptor"]["file"],
                prepared["relative_path"],
            )

    def test_review_rejects_non_three_coordinate_site(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            lines = _three_coordinate_site()[:-1]
            project_dir = self._create_project(root, lines)
            status = get_status(str(project_dir))
            self.assertEqual(status["sites"][0]["coordination_number"], 2)
            response = save_review(
                str(project_dir),
                self._review_payload(status),
            )
            self.assertFalse(response["ok"])
            self.assertEqual(
                response["error"]["code"],
                "AD4ZN_TZ_GEOMETRY_UNSUPPORTED",
            )

    def test_prepare_separates_tz_when_selected_zinc_is_final_line_without_newline(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            site_lines = _three_coordinate_site()
            reordered = site_lines[1:] + [site_lines[0].rstrip("\n")]
            project_dir = self._create_project(root, reordered)
            self._save_valid_review(project_dir)
            status = prepare_receptor(str(project_dir))
            self.assertTrue(status["ok"], status)
            output = project_dir / status["prepared_receptor"]["relative_path"]
            atom_lines = [
                line
                for line in output.read_text(encoding="utf-8").splitlines()
                if line[:6].strip().upper() in {"ATOM", "HETATM"}
            ]
            self.assertEqual(len(atom_lines), 5)
            self.assertEqual(
                sum(line[77:79].strip() == "TZ" for line in atom_lines),
                1,
            )

    def test_parameter_validation_accepts_pinned_fixture_without_returning_content(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "AD4Zn.dat"
            source.write_text(_valid_parameter_text(), encoding="utf-8")
            response = validate_parameter_file(source)
            self.assertTrue(response["ok"], response)
            self.assertEqual(
                response["license_source"],
                "user_provided_gpl_asset",
            )
            self.assertIn("ZN", response["atom_types"])
            self.assertIn("TZ", response["atom_types"])
            self.assertEqual(len(response["coefficients"]), 5)
            self.assertEqual(
                response["supported_profile_id"],
                "autodock_vina_1_2_7_ad4zn",
            )
            self.assertEqual(response["license_id"], "GPL-2.0-or-later")
            self.assertTrue(response["license_notice_detected"])
            self.assertEqual(len(response["canonical_sha256"]), 64)
            self.assertEqual(
                response["reference_hash_basis"],
                "line_endings_lf_v1",
            )
            self.assertTrue(response["matches_reference_sha256"])
            self.assertEqual(len(response["atom_type_table_sha256"]), 64)
            self.assertNotIn("content", response)

    def test_parameter_reference_identity_accepts_lf_and_crlf_only(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            lf_payload = _valid_parameter_text().encode("utf-8")
            crlf_payload = lf_payload.replace(b"\n", b"\r\n")
            reference_sha256 = hashlib.sha256(lf_payload).hexdigest()
            lf_source = Path(root) / "AD4Zn-lf.dat"
            crlf_source = Path(root) / "AD4Zn-crlf.dat"
            tampered_source = Path(root) / "AD4Zn-tampered.dat"
            lf_source.write_bytes(lf_payload)
            crlf_source.write_bytes(crlf_payload)
            tampered_source.write_bytes(crlf_payload + b"# changed\r\n")

            with patch.object(
                ad4zn_module,
                "SUPPORTED_PARAMETER_REFERENCE_SHA256",
                reference_sha256,
            ):
                lf_response = validate_parameter_file(lf_source)
                crlf_response = validate_parameter_file(crlf_source)
                tampered_response = validate_parameter_file(tampered_source)

            self.assertTrue(lf_response["ok"], lf_response)
            self.assertTrue(crlf_response["ok"], crlf_response)
            self.assertNotEqual(lf_response["sha256"], crlf_response["sha256"])
            self.assertEqual(
                lf_response["canonical_sha256"],
                crlf_response["canonical_sha256"],
            )
            self.assertEqual(
                crlf_response["canonical_sha256"],
                reference_sha256,
            )
            self.assertTrue(lf_response["matches_reference_sha256"])
            self.assertTrue(crlf_response["matches_reference_sha256"])
            self.assertTrue(tampered_response["ok"], tampered_response)
            self.assertFalse(tampered_response["matches_reference_sha256"])

    def test_semantic_match_with_nonreference_bytes_is_not_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            source = Path(root) / "AD4Zn-semantic-only.dat"
            source.write_text(
                _valid_parameter_text() + "# semantically harmless change\n",
                encoding="utf-8",
            )

            rejected = record_parameter_file(str(project_dir), source)

            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(
                rejected["error"]["code"],
                "AD4ZN_PARAMETER_REFERENCE_MISMATCH",
            )
            self.assertFalse(
                (project_dir / "ad4zn" / "parameters" / "AD4Zn.dat").exists()
            )
            project = load_project(str(project_dir))
            self.assertTrue(project["ok"], project)
            self.assertNotIn("parameter_file", project["project"].get("ad4zn", {}))

    def test_malformed_non_zinc_atom_parameter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "AD4Zn-malformed-carbon.dat"
            source.write_text(
                _valid_parameter_text().replace(
                    "atom_par C 4.00 0.150 33.5103 -0.00143 0.0 0.0 0 -1 -1 0",
                    "atom_par C 4.00 broken",
                ),
                encoding="utf-8",
            )

            rejected = validate_parameter_file(source)

            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(
                rejected["error"]["code"],
                "AD4ZN_PARAMETER_ATOM_TABLE_INVALID",
            )

    def test_parameter_record_blocks_uncovered_actual_atom_type(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(
                root,
                _three_coordinate_site()
                + [
                    _atom_line(
                        90,
                        "X1",
                        "LIG",
                        "A",
                        900,
                        30,
                        30,
                        30,
                        0.0,
                        "XX",
                    )
                ],
            )
            source = Path(root) / "AD4Zn.dat"
            source.write_text(_valid_parameter_text(), encoding="utf-8")

            rejected = record_parameter_file(str(project_dir), source)

            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(
                rejected["error"]["code"],
                "AD4ZN_PARAMETER_ATOM_TYPES_UNCOVERED",
            )
            self.assertIn("XX", rejected["error"]["raw_error"])
            self.assertFalse(
                (project_dir / "ad4zn" / "parameters" / "AD4Zn.dat").exists()
            )

    def test_ligand_type_change_invalidates_parameter_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            self._record_valid_parameter(project_dir, root)
            ligand = Path(root) / "ligand_unknown.pdbqt"
            ligand.write_text(
                "ROOT\n"
                + _atom_line(
                    1,
                    "X1",
                    "LIG",
                    "A",
                    1,
                    0,
                    0,
                    0,
                    0.0,
                    "XX",
                )
                + "ENDROOT\nTORSDOF 0\n",
                encoding="utf-8",
            )
            imported = import_ligand_pdbqt(str(project_dir), str(ligand))
            self.assertTrue(imported["ok"], imported)

            status = get_status(str(project_dir))

            self.assertFalse(status["parameter_file_valid"], status)
            self.assertFalse(status["preparation_ready"], status)
            self.assertEqual(
                status["atom_type_coverage"]["missing_parameter_atom_types"],
                ["XX"],
            )

    def test_legacy_crlf_reference_flag_remains_valid(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            source = Path(root) / "AD4Zn-crlf.dat"
            lf_payload = _valid_parameter_text().encode("utf-8")
            source.write_bytes(lf_payload.replace(b"\n", b"\r\n"))
            recorded = record_parameter_file(str(project_dir), source)
            self.assertTrue(recorded["parameter_file_valid"], recorded)
            self.assertTrue(
                recorded["parameter_file"]["matches_reference_sha256"]
            )

            project_path = project_dir / "project.json"
            project_data = json.loads(project_path.read_text(encoding="utf-8"))
            parameter_record = project_data["ad4zn"]["parameter_file"]
            parameter_record.pop("canonical_sha256")
            parameter_record.pop("reference_hash_basis")
            parameter_record.pop("atom_type_table_sha256")
            reference_sha256 = hashlib.sha256(lf_payload).hexdigest()
            parameter_record["reference_sha256"] = reference_sha256
            parameter_record["matches_reference_sha256"] = False
            project_path.write_text(
                json.dumps(project_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with patch.object(
                ad4zn_module,
                "SUPPORTED_PARAMETER_REFERENCE_SHA256",
                reference_sha256,
            ):
                status = get_status(str(project_dir))

            self.assertTrue(status["ok"], status)
            self.assertTrue(status["parameter_file_valid"], status)
            self.assertFalse(
                status["parameter_file"]["matches_reference_sha256"]
            )

    def test_bad_parameter_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            missing_coefficient = Path(root) / "missing_coeff.dat"
            missing_coefficient.write_text(
                _valid_parameter_text().replace("FE_coeff_tors 0.2983\n", ""),
                encoding="utf-8",
            )
            response = validate_parameter_file(missing_coefficient)
            self.assertFalse(response["ok"])
            self.assertEqual(
                response["error"]["code"],
                "AD4ZN_PARAMETER_FE_COEFFICIENTS_INVALID",
            )

            missing_tz = Path(root) / "missing_tz.dat"
            missing_tz.write_text(
                _valid_parameter_text().replace(
                    "atom_par TZ 1.00 0.000 0.0000 0.00000 0.0 0.0 0 -1 -1 0\n",
                    "",
                ),
                encoding="utf-8",
            )
            response = validate_parameter_file(missing_tz)
            self.assertFalse(response["ok"])
            self.assertEqual(
                response["error"]["code"],
                "AD4ZN_PARAMETER_ATOM_TYPES_MISSING",
            )

            wrong_profile = Path(root) / "wrong_profile.dat"
            wrong_profile.write_text(
                _valid_parameter_text().replace(
                    "atom_par ZN 1.48 0.550",
                    "atom_par ZN 1.50 0.550",
                ),
                encoding="utf-8",
            )
            response = validate_parameter_file(wrong_profile)
            self.assertFalse(response["ok"])
            self.assertEqual(
                response["error"]["code"],
                "AD4ZN_PARAMETER_PROFILE_MISMATCH",
            )

            binary = Path(root) / "binary.dat"
            binary.write_bytes(b"FE_coeff_vdW\x00bad")
            response = validate_parameter_file(binary)
            self.assertFalse(response["ok"])
            self.assertEqual(
                response["error"]["code"],
                "AD4ZN_PARAMETER_TEXT_INVALID",
            )

    def test_parameter_hash_tamper_invalidates_record(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            self._record_valid_parameter(project_dir, root)
            copied = project_dir / "ad4zn" / "parameters" / "AD4Zn.dat"
            copied.write_text(_valid_parameter_text() + "# tampered\n", encoding="utf-8")
            status = get_status(str(project_dir))
            self.assertFalse(status["parameter_file_valid"])
            self.assertFalse(status["parameter_file"]["valid"])
            self.assertTrue(
                any(
                    item["code"] == "AD4ZN_PARAMETER_REQUIRED"
                    for item in status["issues"]
                )
            )

    def test_prepared_receptor_hash_tamper_invalidates_record(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            self._save_valid_review(project_dir)
            prepared_status = prepare_receptor(str(project_dir))
            self.assertTrue(prepared_status["prepared_receptor_valid"])
            output = project_dir / prepared_status["prepared_receptor"]["relative_path"]
            output.write_text(
                output.read_text(encoding="utf-8") + "REMARK tampered\n",
                encoding="utf-8",
            )
            status = get_status(str(project_dir))
            self.assertFalse(status["prepared_receptor_valid"])
            self.assertFalse(status["prepared_receptor"]["valid"])

    def test_full_preparation_ready_requires_review_receptor_and_parameter(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            self._save_valid_review(project_dir)
            prepare_receptor(str(project_dir))
            status = self._record_valid_parameter(project_dir, root)
            self.assertTrue(status["ready"])
            self.assertTrue(status["preparation_ready"])
            self.assertTrue(status["step_readiness"]["run"])
            self.assertTrue(status["compatibility"]["maps_pipeline_connected"])

    def test_cli_status_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = self._create_project(root, _three_coordinate_site())
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dockstart_core.ad4zn",
                    "status",
                    str(project_dir),
                ],
                cwd=BACKEND_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["protocol_id"], "ad4zn_beta")


if __name__ == "__main__":
    unittest.main()
