from __future__ import annotations

import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "verify_ad4zn_multisample.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_ad4zn_multisample",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _text_record(lf_payload: bytes) -> dict[str, object]:
    crlf_payload = lf_payload.replace(b"\n", b"\r\n")
    return {
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
    }


def _pdb_line(
    serial: int,
    name: str,
    residue: str,
    chain: str,
    residue_number: int,
    coordinate: tuple[float, float, float],
    *,
    record: str = "HETATM",
    element: str = "C",
) -> str:
    return (
        f"{record:<6}{serial:>5} {name:^4} {residue:>3} {chain:1}"
        f"{residue_number:>4}    "
        f"{coordinate[0]:>8.3f}{coordinate[1]:>8.3f}{coordinate[2]:>8.3f}"
        f"  1.00 20.00          {element:>2}"
    )


def _sdf_text(
    name: str,
    coordinates: list[tuple[float, float, float]],
) -> str:
    lines = [
        name,
        "  Synthetic",
        "",
        f"{len(coordinates):>3}{0:>3}  0  0  0  0  0  0  0  0999 V2000",
    ]
    lines.extend(
        (
            f"{x:>10.4f}{y:>10.4f}{z:>10.4f} C   "
            "0  0  0  0  0  0  0  0  0  0  0  0"
        )
        for x, y, z in coordinates
    )
    lines.extend(["M  END", "$$$$", ""])
    return "\n".join(lines)


def _pdbqt_atom(
    serial: int,
    coordinate: tuple[float, float, float],
    atom_type: str = "C",
) -> str:
    return (
        f"ATOM  {serial:5d}  C{serial:<2d} LIG A   1    "
        f"{coordinate[0]:8.3f}{coordinate[1]:8.3f}{coordinate[2]:8.3f}"
        f"  1.00  0.00     0.000 {atom_type}"
    )


def _model(
    mode: int,
    coordinates: list[tuple[float, float, float]],
) -> str:
    return "\n".join(
        [
            f"MODEL {mode}",
            *[
                _pdbqt_atom(index, coordinate)
                for index, coordinate in enumerate(coordinates, start=1)
            ],
            "ENDMDL",
        ]
    )


def _coverage(
    system: dict[str, object],
    protocol: dict[str, object],
) -> dict[str, object]:
    axes = ("x", "y", "z")
    center = dict(zip(axes, system["box"]["center"], strict=True))
    size = dict(zip(axes, system["box"]["size"], strict=True))
    intervals = dict(
        zip(axes, protocol["effective_grid_intervals"], strict=True)
    )
    actual_size = dict(
        zip(axes, protocol["effective_grid_size_angstrom"], strict=True)
    )

    def bounds(
        nested_center: dict[str, float],
        nested_size: dict[str, float],
    ) -> dict[str, dict[str, float]]:
        return {
            "min": {
                axis: nested_center[axis] - nested_size[axis] / 2
                for axis in axes
            },
            "max": {
                axis: nested_center[axis] + nested_size[axis] / 2
                for axis in axes
            },
        }

    coordinates = {
        "ZN": dict(zip(axes, system["zinc"]["coordinate"], strict=True)),
        "TZ": dict(
            zip(
                axes,
                system["ad4zn_site"]["tz_coordinate"],
                strict=True,
            )
        ),
    }
    coverage = {
        "method": VERIFY.AD4ZN_BOX_COVERAGE_METHOD,
        "interval_semantics": "closed",
        "epsilon_angstrom": 0.0,
        "receptor": {"size_bytes": 123, "sha256": "c" * 64},
        "box_center_angstrom": center,
        "requested_box_size_angstrom": size,
        "requested_box_bounds_angstrom": bounds(center, size),
        "effective_grid_spacing_angstrom": protocol["spacing"],
        "effective_grid_axis_intervals": intervals,
        "effective_grid_size_angstrom": actual_size,
        "effective_grid_bounds_angstrom": bounds(center, actual_size),
        "markers": {
            marker: {
                "atom_type": marker,
                "coordinate_angstrom": coordinate,
                "inside_requested_box": True,
                "inside_effective_grid": True,
            }
            for marker, coordinate in coordinates.items()
        },
        "all_zn_tz_inside_requested_box": True,
        "all_zn_tz_inside_effective_grid": True,
    }
    return {
        "grid": {
            "center": center,
            "requested_box": {"center": center, "size": size},
            "grid_points": intervals,
            "spacing": protocol["spacing"],
            "actual_size": actual_size,
        },
        "ad4zn": {
            "box_coverage": coverage,
            "box_coverage_sha256": VERIFY._canonical_json_sha256(coverage),
        },
    }


class AD4ZnMultisampleVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = VERIFY._load_manifest()

    def test_manifest_is_strictly_metadata_only(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(
            manifest["fixture_id"],
            "ad4zn_multisample_external",
        )
        self.assertEqual(manifest["distribution"], "metadata_only")
        self.assertIs(manifest["network_access_allowed"], False)
        for flag in VERIFY.CONTAINS_FLAGS:
            self.assertIs(manifest[flag], False)
        self.assertEqual(
            {item.name for item in VERIFY.MANIFEST_PATH.parent.iterdir()},
            {"README.md", "source_manifest.json"},
        )
        self.assertEqual(set(manifest["source_files"]), set(VERIFY.SOURCE_KEYS))
        for record in manifest["source_files"].values():
            self.assertEqual(record["canonical_line_endings"], "LF")
            self.assertEqual(set(record["checkout_variants"]), {"lf", "crlf"})
            self.assertRegex(record["canonical_sha256"], r"^[0-9a-f]{64}$")

    def test_manifest_pins_two_distinct_system_contracts(self) -> None:
        systems = self.manifest["systems"]
        self.assertEqual(list(systems), ["2OI0", "1R1J"])
        self.assertEqual(
            systems["2OI0"]["ligand_component"]["residue_name"],
            "283",
        )
        self.assertEqual(
            systems["1R1J"]["ligand_component"]["residue_name"],
            "OIR",
        )
        self.assertNotEqual(
            systems["2OI0"]["ad4zn_site"]["tz_coordinate"],
            systems["1R1J"]["ad4zn_site"]["tz_coordinate"],
        )
        self.assertNotEqual(
            systems["2OI0"]["maps"]["ligand_atom_types"],
            systems["1R1J"]["maps"]["ligand_atom_types"],
        )
        self.assertEqual(
            VERIFY._expanded_residue_numbers(
                systems["1R1J"]["receptor_deletions"]
            )[:5],
            [505, 752, 753, 754, 2001],
        )
        self.assertEqual(
            VERIFY._expanded_residue_numbers(
                systems["1R1J"]["receptor_deletions"]
            )[-1],
            2089,
        )
        self.assertIn(
            "A:505,752,753,754,2001,2002",
            VERIFY._deletion_spec(systems["1R1J"]),
        )

    def test_cli_requires_every_caller_path_override(self) -> None:
        parser = VERIFY._parser()
        args = parser.parse_args(
            [
                "--pdb-2oi0",
                "a.pdb",
                "--pdb-1r1j",
                "b.pdb",
                "--ligand-2oi0-sdf",
                "a.sdf",
                "--ligand-1r1j-sdf",
                "b.sdf",
                "--mk-prepare-receptor",
                "mk.exe",
                "--python",
                "python.exe",
                "--autogrid",
                "autogrid.exe",
                "--vina",
                "vina.exe",
                "--ad4zn-dat",
                "AD4Zn.dat",
            ]
        )
        self.assertEqual(args.pdb_2oi0, Path("a.pdb"))
        self.assertEqual(args.pdb_1r1j, Path("b.pdb"))
        self.assertEqual(args.ligand_2oi0_sdf, Path("a.sdf"))
        self.assertEqual(args.ligand_1r1j_sdf, Path("b.sdf"))
        self.assertEqual(args.python_executable, Path("python.exe"))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args([])

    def test_text_identity_accepts_only_pinned_lf_or_crlf(self) -> None:
        lf = b"HEADER synthetic\nATOM scientific\n"
        record = _text_record(lf)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.pdb"
            for style, payload in (
                ("lf", lf),
                ("crlf", lf.replace(b"\n", b"\r\n")),
            ):
                with self.subTest(style=style):
                    path.write_bytes(payload)
                    evidence = VERIFY._verify_text_identity(
                        path,
                        record,
                        label="synthetic",
                    )
                    self.assertEqual(evidence["checkout_variant"], style)
                    self.assertEqual(evidence["canonical_sha256"], _sha256(lf))

            path.write_bytes(b"HEADER changed\nATOM scientific\n")
            with self.assertRaises(
                VERIFY.AD4ZnMultisampleAcceptanceError
            ) as raised:
                VERIFY._verify_text_identity(
                    path,
                    record,
                    label="synthetic",
                )
            self.assertEqual(
                raised.exception.code,
                "AD4ZN_MULTISAMPLE_CANONICAL_HASH_MISMATCH",
            )

            path.write_bytes(b"HEADER synthetic\r\nATOM scientific\n")
            with self.assertRaises(
                VERIFY.AD4ZnMultisampleAcceptanceError
            ) as raised:
                VERIFY._verify_text_identity(
                    path,
                    record,
                    label="synthetic",
                )
            self.assertEqual(
                raised.exception.code,
                "AD4ZN_MULTISAMPLE_LINE_ENDINGS_UNSUPPORTED",
            )

    def test_windows_binary_hash_is_fail_closed_before_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tool.exe"
            path.write_bytes(b"not the pinned executable")
            record = {"validated_windows_x86_64_sha256": "0" * 64}
            with self.assertRaises(
                VERIFY.AD4ZnMultisampleAcceptanceError
            ) as raised:
                VERIFY._enforce_windows_tool_hash(
                    "tool",
                    path,
                    record,
                    platform_name="Windows",
                )
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_MULTISAMPLE_WINDOWS_TOOL_HASH_MISMATCH",
        )

    def test_non_windows_tool_gate_checks_paths_versions_and_capability(
        self,
    ) -> None:
        tools = copy.deepcopy(self.manifest["external_tools"])
        manifest = {"external_tools": tools}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                name: root / name
                for name in ("python", "mk", "autogrid", "vina")
            }
            for path in paths.values():
                path.write_bytes(b"synthetic platform binary")
            probes = [
                subprocess.CompletedProcess(
                    [str(paths["python"]), "--version"],
                    0,
                    "Python 3.11.15\n",
                    "",
                ),
                subprocess.CompletedProcess(
                    [str(paths["python"]), "-I"],
                    0,
                    '{"meeko":"0.7.1","rdkit":"2026.03.3"}\n',
                    "",
                ),
                subprocess.CompletedProcess(
                    [str(paths["mk"]), "--help"],
                    0,
                    "--read_pdb --write_pdbqt --delete_residues\n",
                    "",
                ),
            ]
            autogrid = SimpleNamespace(
                status="ok",
                path=str(paths["autogrid"].resolve()),
                version="4.2.7",
                source="configured",
                message="",
                raw_error="",
                capabilities={},
            )
            vina = SimpleNamespace(
                status="ok",
                path=str(paths["vina"].resolve()),
                version="1.2.7",
                source="configured",
                message="",
                raw_error="",
                capabilities={
                    "features": {
                        "maps": {
                            "supported": True,
                        }
                    }
                },
            )
            with (
                mock.patch.object(
                    VERIFY,
                    "_run_command",
                    side_effect=probes,
                ),
                mock.patch.object(
                    VERIFY.autogrid_adapter,
                    "detect",
                    return_value=autogrid,
                ),
                mock.patch.object(
                    VERIFY.vina_adapter,
                    "detect",
                    return_value=vina,
                ),
            ):
                _autogrid, _vina, evidence = VERIFY._verify_external_tools(
                    python_executable=paths["python"],
                    mk_prepare_receptor=paths["mk"],
                    autogrid_executable=paths["autogrid"],
                    vina_executable=paths["vina"],
                    manifest=manifest,
                    platform_name="Linux",
                )
        self.assertFalse(evidence["fixed_windows_toolchain"])
        for item in evidence["tools"].values():
            self.assertEqual(item["hash_policy"], "recorded_platform_specific")
        self.assertTrue(evidence["tools"]["vina"]["maps_capability"])

    def test_tool_version_mismatch_is_rejected(self) -> None:
        tools = copy.deepcopy(self.manifest["external_tools"])
        manifest = {"external_tools": tools}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / name for name in ("python", "mk", "autogrid", "vina")]
            for path in paths:
                path.write_bytes(b"synthetic")
            with (
                mock.patch.object(
                    VERIFY,
                    "_run_command",
                    return_value=subprocess.CompletedProcess(
                        ["python", "--version"],
                        0,
                        "Python 3.10.0\n",
                        "",
                    ),
                ),
                self.assertRaises(
                    VERIFY.AD4ZnMultisampleAcceptanceError
                ) as raised,
            ):
                VERIFY._verify_external_tools(
                    python_executable=paths[0],
                    mk_prepare_receptor=paths[1],
                    autogrid_executable=paths[2],
                    vina_executable=paths[3],
                    manifest=manifest,
                    platform_name="Linux",
                )
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_MULTISAMPLE_PYTHON_VERSION_MISMATCH",
        )

    def test_synthetic_1r1j_contract_proves_a505_is_remote(self) -> None:
        coordinates = [
            (float(index), 0.0, 0.0)
            for index in range(28)
        ]
        system = copy.deepcopy(self.manifest["systems"]["1R1J"])
        system["raw_water_count"] = 2
        system["raw_water_residue_range"] = [2002, 2003]
        system["raw_glycan_residues"]["atom_count"] = 3
        system["receptor_deletions"]["residue_numbers"]["ranges"] = [
            [2002, 2003]
        ]
        lines: list[str] = []
        serial = 1
        for coordinate in coordinates:
            lines.append(
                _pdb_line(
                    serial,
                    f"C{serial}",
                    "OIR",
                    "A",
                    2001,
                    coordinate,
                )
            )
            serial += 1
        lines.append(
            _pdb_line(serial, "ZN", "ZN", "A", 1001, (31.27, 43.033, 29.617), element="ZN")
        )
        serial += 1
        for residue in (752, 753, 754):
            lines.append(
                _pdb_line(
                    serial,
                    "C1",
                    "NAG",
                    "A",
                    residue,
                    (0.0, 0.0, 0.0),
                )
            )
            serial += 1
        lines.append(
            _pdb_line(
                serial,
                "CA",
                "GLU",
                "A",
                505,
                (-10.0, 0.0, 0.0),
                record="ATOM",
            )
        )
        serial += 1
        for residue in (2002, 2003):
            lines.append(
                _pdb_line(
                    serial,
                    "O",
                    "HOH",
                    "A",
                    residue,
                    (0.0, 0.0, 0.0),
                    element="O",
                )
            )
            serial += 1
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdb = root / "1R1J.pdb"
            sdf = root / "1R1J.sdf"
            pdb.write_text("\n".join(lines) + "\n", encoding="utf-8")
            sdf.write_text(_sdf_text("OIR", coordinates), encoding="utf-8")
            evidence = VERIFY._validate_raw_system(
                "1R1J",
                pdb,
                sdf,
                system,
            )
        self.assertGreater(
            evidence["a505_minimum_distance_to_zinc_angstrom"],
            35.0,
        )
        self.assertEqual(evidence["nag_atom_count"], 3)
        self.assertIn("A:505,752,753,754,2001,2002,2003", evidence["receptor_deletion_spec"])

    def test_box_coverage_validates_hash_requested_and_effective_grid(
        self,
    ) -> None:
        system = copy.deepcopy(self.manifest["systems"]["2OI0"])
        protocol = copy.deepcopy(self.manifest["protocol"])
        maps_manifest = _coverage(system, protocol)
        evidence = VERIFY._validate_box_coverage(
            maps_manifest,
            system,
            protocol,
        )
        self.assertTrue(evidence["all_zn_tz_inside_requested_box"])
        self.assertTrue(evidence["all_zn_tz_inside_effective_grid"])
        self.assertEqual(
            evidence["effective_grid_axis_intervals"],
            {"x": 54, "y": 54, "z": 54},
        )

        tampered = copy.deepcopy(maps_manifest)
        tampered["ad4zn"]["box_coverage"]["markers"]["TZ"][
            "inside_requested_box"
        ] = False
        tampered["ad4zn"]["box_coverage_sha256"] = (
            VERIFY._canonical_json_sha256(
                tampered["ad4zn"]["box_coverage"]
            )
        )
        with self.assertRaises(
            VERIFY.AD4ZnMultisampleAcceptanceError
        ) as raised:
            VERIFY._validate_box_coverage(tampered, system, protocol)
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_MULTISAMPLE_BOX_COVERAGE_MARKER_FAILED",
        )

    def test_report_oracle_requires_protocol_disclaimer_and_coverage(
        self,
    ) -> None:
        system = copy.deepcopy(self.manifest["systems"]["2OI0"])
        protocol = copy.deepcopy(self.manifest["protocol"])
        maps_manifest = _coverage(system, protocol)
        coverage = maps_manifest["ad4zn"]["box_coverage"]
        report = "\n".join(
            [
                *protocol["report_required_substrings"],
                *VERIFY._coverage_report_rows(coverage),
            ]
        )
        evidence = VERIFY._validate_report_evidence(
            report,
            protocol["report_required_substrings"],
            coverage,
        )
        self.assertTrue(evidence["all_required_evidence_present"])
        with self.assertRaises(
            VERIFY.AD4ZnMultisampleAcceptanceError
        ) as raised:
            VERIFY._validate_report_evidence(
                report.replace(
                    "Docking score 仅供结构结合趋势参考，不能替代实验验证。",
                    "",
                ),
                protocol["report_required_substrings"],
                coverage,
            )
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_MULTISAMPLE_REPORT_EVIDENCE_MISSING",
        )

    def test_repeatability_exact_hash_is_windows_evidence_only(self) -> None:
        payload = b"same deterministic output"
        portable = VERIFY._validate_repeatability(
            payload,
            payload,
            fixed_windows_expected_sha256="0" * 64,
            enforce_fixed_windows_sha256=False,
        )
        self.assertTrue(portable["byte_identical"])
        self.assertFalse(portable["fixed_windows_sha256_enforced"])
        with self.assertRaises(
            VERIFY.AD4ZnMultisampleAcceptanceError
        ) as raised:
            VERIFY._validate_repeatability(
                payload,
                payload,
                fixed_windows_expected_sha256="0" * 64,
                enforce_fixed_windows_sha256=True,
            )
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_MULTISAMPLE_WINDOWS_OUTPUT_HASH_MISMATCH",
        )
        with self.assertRaises(
            VERIFY.AD4ZnMultisampleAcceptanceError
        ) as raised:
            VERIFY._validate_repeatability(
                payload,
                b"different output",
                fixed_windows_expected_sha256="",
                enforce_fixed_windows_sha256=False,
            )
        self.assertEqual(
            raised.exception.code,
            "AD4ZN_MULTISAMPLE_REPEAT_OUTPUT_MISMATCH",
        )

    def test_direct_rmsd_is_same_order_unsymmetrized_and_not_best_score(
        self,
    ) -> None:
        reference = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ligand = root / "ligand.pdbqt"
            output = root / "out.pdbqt"
            ligand.write_text(
                "\n".join(
                    [
                        _pdbqt_atom(index, coordinate)
                        for index, coordinate in enumerate(reference, start=1)
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            output.write_text(
                "\n".join(
                    [
                        _model(1, [(4.0, 0.0, 0.0), (6.0, 0.0, 0.0)]),
                        _model(2, [(0.1, 0.0, 0.0), (2.1, 0.0, 0.0)]),
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            evidence = VERIFY._direct_heavy_rmsd_diagnostics(
                ligand,
                output,
                [
                    {"mode": 1, "affinity": -10.0},
                    {"mode": 2, "affinity": -9.0},
                ],
                maximum_minimum_rmsd=1.0,
            )
        self.assertEqual(evidence["best_score_mode"]["mode"], 1)
        self.assertEqual(evidence["lowest_direct_rmsd_mode"]["mode"], 2)
        self.assertFalse(evidence["symmetry_corrected"])
        self.assertFalse(evidence["alignment_applied"])
        self.assertTrue(
            evidence[
                "best_score_mode_is_not_required_to_be_best_rmsd_mode"
            ]
        )

    def test_failure_restores_settings_and_removes_both_project_root(
        self,
    ) -> None:
        captured: list[Path] = []
        manifest = {
            "fixture_id": "ad4zn_multisample_external",
            "distribution": "metadata_only",
        }
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary)
            tools = {
                name: external / name
                for name in ("python", "mk", "autogrid", "vina")
            }
            for path in tools.values():
                path.write_bytes(b"synthetic")
            source_files = {
                key: external / f"{key}.txt"
                for key in VERIFY.SOURCE_KEYS
            }
            for path in source_files.values():
                path.write_text("synthetic", encoding="utf-8")

            def fail_workflow(
                _system_id: str,
                *,
                work_root: Path,
                **_kwargs: object,
            ) -> dict[str, object]:
                captured.append(work_root)
                (work_root / "sentinel.txt").write_text(
                    "temporary",
                    encoding="utf-8",
                )
                raise VERIFY.AD4ZnMultisampleAcceptanceError(
                    "TEST_SAMPLE_FAILURE",
                    "synthetic sample failure",
                )

            with (
                mock.patch.object(
                    VERIFY,
                    "_load_manifest",
                    return_value=manifest,
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_source_inputs",
                    return_value=(
                        source_files,
                        {"identity": "synthetic"},
                    ),
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_external_tools",
                    return_value=(
                        object(),
                        object(),
                        {"fixed_windows_toolchain": False},
                    ),
                ),
                mock.patch.object(VERIFY, "save_settings"),
                mock.patch.object(
                    VERIFY,
                    "_run_sample_workflow",
                    side_effect=fail_workflow,
                ),
                mock.patch.dict(
                    os.environ,
                    {VERIFY.SETTINGS_ENV_VAR: "original-settings.json"},
                    clear=False,
                ),
            ):
                with self.assertRaises(
                    VERIFY.AD4ZnMultisampleAcceptanceError
                ) as raised:
                    VERIFY.verify_ad4zn_multisample(
                        pdb_2oi0=source_files["2oi0_pdb"],
                        pdb_1r1j=source_files["1r1j_pdb"],
                        ligand_2oi0_sdf=source_files["2oi0_ligand_sdf"],
                        ligand_1r1j_sdf=source_files["1r1j_ligand_sdf"],
                        mk_prepare_receptor=tools["mk"],
                        python_executable=tools["python"],
                        autogrid_executable=tools["autogrid"],
                        vina_executable=tools["vina"],
                        ad4zn_dat=source_files["ad4zn_parameter"],
                    )
                self.assertEqual(
                    os.environ[VERIFY.SETTINGS_ENV_VAR],
                    "original-settings.json",
                )
        self.assertEqual(raised.exception.code, "TEST_SAMPLE_FAILURE")
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0].exists())
        self.assertTrue(
            raised.exception.details["temporary_cleanup"]["removed"]
        )
        self.assertTrue(
            raised.exception.details["settings_environment"]["restored"]
        )


if __name__ == "__main__":
    unittest.main()
