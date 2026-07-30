from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "verify_ad4zn_1s63.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_ad4zn_1s63",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _upstream_manifest(lf_payload: bytes) -> dict[str, object]:
    crlf_payload = lf_payload.replace(b"\n", b"\r\n")
    return {
        "upstream": {
            "repository": "https://example.invalid/AutoDock-Vina",
            "tag": "v1.2.7",
            "commit": "a" * 40,
        },
        "required_files": {
            "parameter_file": {
                "path": "data/AD4Zn.dat",
                "git_blob_sha1": "b" * 40,
                "canonical_line_endings": "LF",
                "canonical_size_bytes": len(lf_payload),
                "canonical_sha256": _sha256(lf_payload),
                "checkout_variants": {
                    "lf": {
                        "size_bytes": len(lf_payload),
                        "sha256": _sha256(lf_payload),
                    },
                    "crlf": {
                        "size_bytes": len(crlf_payload),
                        "sha256": _sha256(crlf_payload),
                    },
                },
            },
        },
    }


def _bounds(
    center: dict[str, float],
    size: dict[str, float],
) -> dict[str, dict[str, float]]:
    return {
        "min": {
            axis: center[axis] - size[axis] / 2.0
            for axis in ("x", "y", "z")
        },
        "max": {
            axis: center[axis] + size[axis] / 2.0
            for axis in ("x", "y", "z")
        },
    }


class AD4Zn1s63VerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = VERIFY._load_manifest()

    def _coverage_manifest(self) -> tuple[dict[str, object], dict[str, object]]:
        expected = copy.deepcopy(self.manifest["expected"])
        grid = expected["grid"]
        site = expected["site"]
        center = {
            axis: float(value)
            for axis, value in zip(
                ("x", "y", "z"),
                grid["center"],
                strict=True,
            )
        }
        points = {
            axis: int(value)
            for axis, value in zip(
                ("x", "y", "z"),
                grid["grid_points"],
                strict=True,
            )
        }
        spacing = float(grid["spacing"])
        size = {
            axis: points[axis] * spacing
            for axis in ("x", "y", "z")
        }
        coordinates = {
            "ZN": {
                axis: float(value)
                for axis, value in zip(
                    ("x", "y", "z"),
                    site["zinc_coordinate"],
                    strict=True,
                )
            },
            "TZ": {
                axis: float(value)
                for axis, value in zip(
                    ("x", "y", "z"),
                    site["tz_coordinate"],
                    strict=True,
                )
            },
        }
        coverage = {
            "method": VERIFY.AD4ZN_BOX_COVERAGE_METHOD,
            "interval_semantics": "closed",
            "epsilon_angstrom": 1e-9,
            "receptor": {"size_bytes": 123, "sha256": "c" * 64},
            "box_center_angstrom": center,
            "requested_box_size_angstrom": size,
            "requested_box_bounds_angstrom": _bounds(center, size),
            "effective_grid_spacing_angstrom": spacing,
            "effective_grid_axis_intervals": points,
            "effective_grid_size_angstrom": size,
            "effective_grid_bounds_angstrom": _bounds(center, size),
            "markers": {
                marker_type: {
                    "atom_type": marker_type,
                    "coordinate_angstrom": coordinate,
                    "inside_requested_box": True,
                    "inside_effective_grid": True,
                }
                for marker_type, coordinate in coordinates.items()
            },
            "all_zn_tz_inside_requested_box": True,
            "all_zn_tz_inside_effective_grid": True,
        }
        maps_manifest = {
            "grid": {
                "center": center,
                "requested_box": {
                    "center": center,
                    "size": size,
                },
                "grid_points": points,
                "spacing": spacing,
                "actual_size": size,
            },
            "ad4zn": {
                "box_coverage": coverage,
                "box_coverage_sha256": VERIFY._canonical_json_sha256(
                    coverage
                ),
            },
        }
        return maps_manifest, expected

    def test_canonical_lf_changes_crlf_only(self) -> None:
        self.assertEqual(
            VERIFY._canonical_lf(b"alpha \t\r\nbeta\n\xff"),
            b"alpha \t\nbeta\n\xff",
        )
        self.assertEqual(
            VERIFY._canonical_lf(b"alpha\rbeta"),
            b"alpha\rbeta",
        )

    def test_upstream_gate_accepts_pinned_lf_and_crlf(self) -> None:
        lf_payload = b"atom_par ZN 1.0 2.0\natom_par TZ 3.0 4.0\n"
        crlf_payload = lf_payload.replace(b"\n", b"\r\n")
        manifest = _upstream_manifest(lf_payload)

        for variant, payload in (("lf", lf_payload), ("crlf", crlf_payload)):
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary).resolve()
                    parameter = root / "data" / "AD4Zn.dat"
                    parameter.parent.mkdir()
                    parameter.write_bytes(payload)
                    with mock.patch.object(VERIFY, "_run_probe") as run_probe:
                        _files, evidence = VERIFY._verify_upstream(
                            root,
                            manifest,
                        )
                    run_probe.assert_not_called()
                record = evidence["files"]["parameter_file"]
                self.assertEqual(record["checkout_variant"], variant)
                self.assertEqual(record["line_endings"]["style"], variant)
                self.assertEqual(
                    record["canonical_sha256"],
                    _sha256(lf_payload),
                )

    def test_upstream_gate_rejects_non_line_ending_change(self) -> None:
        lf_payload = b"atom_par ZN 1.0 2.0\natom_par TZ 3.0 4.0\n"
        manifest = _upstream_manifest(lf_payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parameter = root / "data" / "AD4Zn.dat"
            parameter.parent.mkdir()
            parameter.write_bytes(
                b"atom_par ZN 1.0 2.0\natom_par TZ 3.0 4.1\n"
            )
            with self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised:
                VERIFY._verify_upstream(root, manifest)
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_ACCEPTANCE_UPSTREAM_CANONICAL_HASH_MISMATCH",
        )

    def test_upstream_gate_rejects_mixed_line_endings_explicitly(self) -> None:
        lf_payload = b"atom_par ZN 1.0 2.0\natom_par TZ 3.0 4.0\n"
        manifest = _upstream_manifest(lf_payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parameter = root / "data" / "AD4Zn.dat"
            parameter.parent.mkdir()
            parameter.write_bytes(
                b"atom_par ZN 1.0 2.0\r\natom_par TZ 3.0 4.0\n"
            )
            with self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised:
                VERIFY._verify_upstream(root, manifest)
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_ACCEPTANCE_UPSTREAM_LINE_ENDINGS_UNSUPPORTED",
        )

    def test_upstream_gate_requires_exact_lf_crlf_variants(self) -> None:
        lf_payload = b"atom_par ZN 1.0 2.0\n"
        manifest = _upstream_manifest(lf_payload)
        manifest["required_files"]["parameter_file"]["checkout_variants"][
            "mixed"
        ] = {
            "size_bytes": len(lf_payload),
            "sha256": _sha256(lf_payload),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parameter = root / "data" / "AD4Zn.dat"
            parameter.parent.mkdir()
            parameter.write_bytes(lf_payload)
            with self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised:
                VERIFY._verify_upstream(root, manifest)
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
        )

    def test_tool_gate_rejects_vina_path_substitution(self) -> None:
        manifest = {
            "external_tools": {
                "autogrid": {"minimum_version": "4.2.7"},
                "vina": {"required_version": "1.2.7"},
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            autogrid = root / "autogrid4.exe"
            supplied_vina = root / "supplied-vina.exe"
            substituted_vina = root / "bundled-vina.exe"
            for path in (autogrid, supplied_vina, substituted_vina):
                path.write_bytes(b"synthetic executable")
            autogrid_detection = SimpleNamespace(
                status="ok",
                path=str(autogrid),
                version="4.2.7",
                message="",
                raw_error="",
                source="configured",
            )
            vina_detection = SimpleNamespace(
                status="ok",
                path=str(substituted_vina),
                version="1.2.7",
                message="",
                raw_error="",
                source="bundled",
                capabilities={
                    "features": {"maps": {"supported": True}},
                },
            )
            with (
                mock.patch.object(
                    VERIFY.autogrid_adapter,
                    "detect",
                    return_value=autogrid_detection,
                ),
                mock.patch.object(
                    VERIFY.vina_adapter,
                    "detect",
                    return_value=vina_detection,
                ) as vina_detect,
                self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised,
            ):
                VERIFY._verify_tools(
                    autogrid,
                    supplied_vina,
                    manifest,
                )

        self.assertEqual(
            raised.exception.code,
            "AD4ZN_ACCEPTANCE_VINA_PATH_MISMATCH",
        )
        self.assertEqual(
            Path(raised.exception.details["supplied"]).name,
            "supplied-vina.exe",
        )
        self.assertEqual(
            Path(raised.exception.details["detected"]).name,
            "bundled-vina.exe",
        )
        args, kwargs = vina_detect.call_args
        self.assertEqual(Path(args[0]).name, "supplied-vina.exe")
        self.assertIn("bundled_path", kwargs)
        self.assertNotEqual(
            VERIFY._normalized_path(kwargs["bundled_path"]),
            VERIFY._normalized_path(args[0]),
        )

    def test_manifest_is_metadata_only_and_complete(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["fixture_id"], "ad4zn_1s63_external")
        self.assertEqual(manifest["distribution"], "metadata_only")
        for key in VERIFY.MANIFEST_CONTAINS_FLAGS:
            self.assertIs(manifest[key], False)
        self.assertEqual(
            set(manifest["required_files"]),
            set(VERIFY.MANIFEST_REQUIRED_FILES),
        )
        self.assertEqual(
            {item.name for item in VERIFY.MANIFEST_PATH.parent.iterdir()},
            {"README.md", "source_manifest.json"},
        )
        for record in manifest["required_files"].values():
            self.assertEqual(record["canonical_line_endings"], "LF")
            self.assertEqual(
                set(record["checkout_variants"]),
                {"lf", "crlf"},
            )
            self.assertEqual(
                record["checkout_variants"]["lf"]["size_bytes"],
                record["canonical_size_bytes"],
            )
            self.assertEqual(
                record["checkout_variants"]["lf"]["sha256"],
                record["canonical_sha256"],
            )
            self.assertRegex(record["canonical_sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(record["canonical_size_bytes"], 0)

    def test_manifest_contract_rejects_bundled_upstream_artifact(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["contains_parameter_files"] = True
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            path = fixture / "source_manifest.json"
            (fixture / "README.md").write_text("metadata only", encoding="utf-8")
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with (
                mock.patch.object(VERIFY, "MANIFEST_PATH", path),
                self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised,
            ):
                VERIFY._load_manifest()
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
        )

    def test_manifest_pins_scientific_contract(self) -> None:
        manifest = self.manifest
        site = manifest["expected"]["site"]
        grid = manifest["expected"]["grid"]
        maps = manifest["expected"]["map_numeric_equivalence"]
        vina = manifest["expected"]["vina"]
        parameter = manifest["required_files"]["parameter_file"]
        external_parameter = manifest["external_tools"]["parameter_file"]

        self.assertEqual(site["site_count"], 1)
        self.assertEqual(
            site["selected_site_id"],
            "ZN:7206:B:ZN:1001:L7207",
        )
        self.assertEqual(site["coordination_number"], 3)
        self.assertEqual(site["zinc_coordinate"], [18.142, 132.126, 5.224])
        self.assertEqual(site["tz_coordinate"], [19.113, 132.964, 3.689])
        self.assertEqual(site["tz_distance_angstrom"], 2.0)
        self.assertEqual(grid["center"], [18.0, 134.0, -1.0])
        self.assertEqual(grid["grid_points"], [40, 30, 50])
        self.assertEqual(grid["spacing"], 0.375)
        self.assertEqual(
            maps["point_count_per_map"],
            (40 + 1) * (30 + 1) * (50 + 1),
        )
        self.assertEqual(
            maps["map_types"],
            ["A", "C", "Cl", "HD", "N", "NA", "OA", "e", "d"],
        )
        self.assertEqual(
            parameter["canonical_sha256"],
            "12b45d377f081c9f3dc25fba2d4585bb01b8367023f309769e441b8457fb1c00",
        )
        self.assertEqual(external_parameter["license"], "GPL-2.0-or-later")
        self.assertIs(external_parameter["bundled_by_dockstart"], False)
        self.assertEqual(vina["validated_version"], "1.2.7")
        self.assertEqual(vina["scoring"], "ad4")
        self.assertEqual(vina["exhaustiveness"], 32)
        self.assertEqual(vina["num_modes"], 9)
        self.assertEqual(vina["energy_range"], 3)
        self.assertEqual(vina["cpu"], 1)
        self.assertEqual(vina["seed"], 1984557646)
        normalization = vina["output_normalization"]
        self.assertEqual(
            normalization["method"],
            "torsdof_endmdl_nul_padding_v1",
        )
        self.assertIs(normalization["raw_evidence_required_when_changed"], True)
        observation = normalization[
            "windows_vina_1_2_7_observation"
        ]
        self.assertEqual(
            observation["recognized_padding_blocks"]
            * observation["padding_bytes_per_block"],
            observation["nul_bytes_removed"],
        )
        self.assertIn(
            "Docking score 仅供结构结合趋势参考",
            manifest["expected"]["report"]["required_substrings"],
        )

    def test_failure_restores_settings_and_removes_temporary_project(
        self,
    ) -> None:
        captured_root: list[Path] = []

        def fail_project_setup(
            temporary_root: Path,
            _files: object,
            _expected: object,
        ) -> tuple[Path, dict[str, object]]:
            captured_root.append(temporary_root)
            (temporary_root / "sentinel.txt").write_text(
                "temporary",
                encoding="utf-8",
            )
            raise VERIFY.AD4ZnAcceptanceError(
                "TEST_PROJECT_SETUP_FAILURE",
                "synthetic project setup failure",
            )

        manifest = {
            "fixture_id": "ad4zn_1s63_external",
            "distribution": "metadata_only",
            "expected": {},
        }
        with (
            mock.patch.object(VERIFY, "_load_manifest", return_value=manifest),
            mock.patch.object(
                VERIFY,
                "_verify_upstream",
                return_value=({}, {"identity": "synthetic"}),
            ),
            mock.patch.object(
                VERIFY,
                "_verify_tools",
                return_value=(object(), object(), {"tools": "synthetic"}),
            ),
            mock.patch.object(
                VERIFY,
                "_prepare_project",
                side_effect=fail_project_setup,
            ),
            mock.patch.dict(
                os.environ,
                {VERIFY.SETTINGS_ENV_VAR: "original-settings.json"},
                clear=False,
            ),
        ):
            with self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised:
                VERIFY.verify_ad4zn_1s63(
                    Path("synthetic-upstream"),
                    Path("synthetic-autogrid"),
                    Path("synthetic-vina"),
                )
            self.assertEqual(
                os.environ[VERIFY.SETTINGS_ENV_VAR],
                "original-settings.json",
            )

        self.assertEqual(len(captured_root), 1)
        self.assertFalse(captured_root[0].exists())
        self.assertEqual(
            raised.exception.steps["project_setup"]["error"]["code"],
            "TEST_PROJECT_SETUP_FAILURE",
        )
        cleanup = raised.exception.details["temporary_cleanup"]
        self.assertTrue(cleanup["cleanup_called"])
        self.assertTrue(cleanup["removed"])
        self.assertEqual(cleanup["error"], "")
        settings = raised.exception.details["settings_environment"]
        self.assertTrue(settings["previously_set"])
        self.assertTrue(settings["restored"])
        self.assertEqual(settings["error"], "")

    def test_manifest_coordinates_are_an_independent_oracle(self) -> None:
        expected = {
            "zinc_coordinate": [0.0, 0.0, 0.0],
            "tz_coordinate": [2.0, 0.0, 0.0],
            "coordinate_tolerance_angstrom": 0.001,
            "tz_distance_angstrom": 2.0,
            "tz_distance_tolerance_angstrom": 0.001,
        }
        accepted = VERIFY._validate_site_coordinate_oracle(
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            expected,
        )
        self.assertEqual(accepted["generated_distance_angstrom"], 2.0)

        changed_oracle = copy.deepcopy(expected)
        changed_oracle["zinc_coordinate"] = [0.1, 0.0, 0.0]
        with self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised:
            VERIFY._validate_site_coordinate_oracle(
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                (2.0, 0.0, 0.0),
                (2.0, 0.0, 0.0),
                changed_oracle,
            )
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_ACCEPTANCE_SITE_COORDINATE_ORACLE_MISMATCH",
        )

    def test_gpf_center_is_derived_from_manifest(self) -> None:
        lines = VERIFY._gpf_profile_lines(
            {"center": [1.25, -2.0, 3.5], "spacing": 0.25},
            [12, 14, 16],
        )
        self.assertIn("gridcenter 1.25 -2 3.5", lines)
        self.assertIn("spacing 0.25", lines)
        self.assertNotIn("gridcenter 18 134 -1", lines)

    def test_box_coverage_sha_geometry_and_report_evidence(self) -> None:
        maps_manifest, expected = self._coverage_manifest()
        evidence = VERIFY._validate_ad4zn_box_coverage(
            maps_manifest,
            expected,
        )
        self.assertTrue(evidence["all_zn_tz_inside_requested_box"])
        self.assertTrue(evidence["all_zn_tz_inside_effective_grid"])
        rows = VERIFY._report_box_coverage_rows(evidence)
        report = "\n".join(rows)
        report_evidence = VERIFY._validate_report_box_coverage(
            report,
            evidence,
        )
        self.assertEqual(report_evidence["required_row_count"], 6)

        with self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised:
            VERIFY._validate_report_box_coverage(
                "\n".join(rows[:-1]),
                evidence,
            )
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_ACCEPTANCE_REPORT_BOX_COVERAGE_MISSING",
        )

    def test_box_coverage_rejects_hash_or_inclusion_tampering(self) -> None:
        maps_manifest, expected = self._coverage_manifest()
        hash_tampered = copy.deepcopy(maps_manifest)
        hash_tampered["ad4zn"]["box_coverage_sha256"] = "0" * 64
        with self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised:
            VERIFY._validate_ad4zn_box_coverage(hash_tampered, expected)
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_ACCEPTANCE_BOX_COVERAGE_SHA256_MISMATCH",
        )

        inclusion_tampered = copy.deepcopy(maps_manifest)
        coverage = inclusion_tampered["ad4zn"]["box_coverage"]
        coverage["markers"]["TZ"]["inside_requested_box"] = False
        coverage["all_zn_tz_inside_requested_box"] = False
        inclusion_tampered["ad4zn"]["box_coverage_sha256"] = (
            VERIFY._canonical_json_sha256(coverage)
        )
        with self.assertRaises(VERIFY.AD4ZnAcceptanceError) as raised:
            VERIFY._validate_ad4zn_box_coverage(
                inclusion_tampered,
                expected,
            )
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_ACCEPTANCE_BOX_COVERAGE_FAILED",
        )


if __name__ == "__main__":
    unittest.main()
