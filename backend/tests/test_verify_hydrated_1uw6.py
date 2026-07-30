from __future__ import annotations

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
SCRIPT_PATH = ROOT / "scripts" / "verify_hydrated_1uw6.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_hydrated_1uw6",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class _ToolResult(SimpleNamespace):
    def to_dict(self) -> dict[str, object]:
        return dict(vars(self))


def _tool_result(
    path: Path,
    *,
    version: str,
    source: str = "configured",
    is_bundled: bool = False,
    capabilities: dict[str, object] | None = None,
) -> _ToolResult:
    return _ToolResult(
        status="ok",
        path=str(path),
        version=version,
        source=source,
        is_bundled=is_bundled,
        capabilities=capabilities or {},
        message="",
        raw_error="",
    )


def _map_payload(values: list[float]) -> str:
    return "\n".join(
        [
            "GRID_PARAMETER_FILE reference.gpf",
            "GRID_DATA_FILE reference.maps.fld",
            "MACROMOLECULE receptor.pdbqt",
            "SPACING 0.375",
            "NELEMENTS 2 2 2",
            "CENTER 0 0 0",
            *(f"{value:.4f}" for value in values),
            "",
        ]
    )


def _synthetic_pose_reference_and_models(
    *,
    coordinate_shift: float = 0.0,
) -> tuple[dict[str, object], list[list[str]]]:
    contract = VERIFY.PINNED_POSE_RECOVERY_CONTRACT
    elements = tuple(str(value) for value in contract["reference_elements"])
    coordinates = tuple(
        (float(index * 3), float(index % 3), float(-index))
        for index in range(len(elements))
    )
    mapping = [
        int(value)
        for value in contract[
            "smiles_to_reference_sdf_atom_zero_based"
        ]
    ]
    pairs = [
        int(value)
        for value in contract["pdbqt_smiles_idx_pairs_one_based"]
    ]
    serial_to_smiles = {
        pairs[offset + 1]: pairs[offset] - 1
        for offset in range(0, len(pairs), 2)
    }
    models: list[list[str]] = []
    for mode in range(1, 4):
        lines = [
            f"MODEL {mode}",
            f"REMARK SMILES {contract['pdbqt_smiles']}",
            "REMARK SMILES IDX " + " ".join(str(value) for value in pairs),
        ]
        for serial in sorted(serial_to_smiles):
            smiles_index = serial_to_smiles[serial]
            reference_index = mapping[smiles_index]
            x, y, z = coordinates[reference_index]
            x += coordinate_shift
            element = elements[reference_index]
            atom_type = "N" if element == "N" else "C"
            lines.append(
                (
                    f"ATOM  {serial:5d}  {element:<3} UNL     1    "
                    f"{x:8.3f}{y:8.3f}{z:8.3f}"
                    f"  1.00  0.00     0.000 {atom_type}"
                )
            )
        lines.append("ENDMDL")
        models.append(lines)
    return (
        {
            "coordinates": coordinates,
            "elements": elements,
            "evidence": {"synthetic": True},
        },
        models,
    )


def _synthetic_reference_sdf() -> str:
    contract = VERIFY.PINNED_POSE_RECOVERY_CONTRACT
    elements = [str(value) for value in contract["reference_elements"]]
    bonds = contract["reference_bonds_one_based"]
    atom_lines = [
        (
            f"{float(index):10.4f}{0.0:10.4f}{0.0:10.4f} "
            f"{element:<3} 0  0  0  0  0  0  0  0  0  0  0  0"
        )
        for index, element in enumerate(elements)
    ]
    bond_lines = [
        f"{int(first):3d}{int(second):3d}{int(order):3d}  0  0  0  0"
        for first, second, order in bonds
    ]
    return "\n".join(
        [
            "synthetic",
            "  DockStart",
            "",
            f"{len(elements):3d}{len(bonds):3d}  0  0  1  0  0  0  0  0  0",
            *atom_lines,
            *bond_lines,
            "M  END",
            "$$$$",
            "",
        ]
    )


class Hydrated1uw6VerifierTests(unittest.TestCase):
    def _manifest(self, crlf_payload: bytes) -> dict[str, object]:
        canonical = VERIFY._canonical_lf_bytes(crlf_payload)
        return {
            "upstream": {
                "tag": "v1.2.7",
                "commit": "pinned-by-portable-content",
                "checkout_profile": {
                    "portable_identity_algorithm": (
                        VERIFY.PORTABLE_IDENTITY_ALGORITHM
                    ),
                    "accepted_worktree_line_endings": ["LF", "CRLF"],
                },
            },
            "required_files": {
                "map_W": {
                    "path": "example/reference.W.map",
                    "git_blob_sha1": "a" * 40,
                    "size_bytes": len(crlf_payload),
                    "sha256": _sha256(crlf_payload),
                    "portable_text_identity": {
                        "algorithm": VERIFY.PORTABLE_IDENTITY_ALGORITHM,
                        "size_bytes": len(canonical),
                        "sha256": _sha256(canonical),
                    },
                },
            },
            "expected": {
                "water_map": {
                    "portable_lf_sha256": _sha256(canonical),
                },
            },
        }

    def test_canonical_lf_bytes_changes_line_endings_only(self) -> None:
        self.assertEqual(
            VERIFY._canonical_lf_bytes(b"alpha\r\nbeta\rgamma\n"),
            b"alpha\nbeta\ngamma\n",
        )
        self.assertEqual(
            VERIFY._canonical_lf_bytes(b"alpha \t\n\xff"),
            b"alpha \t\n\xff",
        )

    def test_upstream_gate_accepts_lf_and_crlf_with_same_content(self) -> None:
        crlf_payload = b"GRID_PARAMETER_FILE reference.gpf\r\n-0.200\r\n"
        lf_payload = VERIFY._canonical_lf_bytes(crlf_payload)
        manifest = self._manifest(crlf_payload)

        for line_endings, payload, raw_match in (
            ("crlf", crlf_payload, True),
            ("lf", lf_payload, False),
        ):
            with self.subTest(line_endings=line_endings):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    path = root / "example" / "reference.W.map"
                    path.parent.mkdir()
                    path.write_bytes(payload)

                    _, evidence = VERIFY._verify_upstream(root, manifest)

                record = evidence["files"]["map_W"]
                self.assertEqual(record["line_endings"]["style"], line_endings)
                self.assertEqual(
                    record["portable_identity"]["sha256"],
                    manifest["expected"]["water_map"][
                        "portable_lf_sha256"
                    ],
                )
                self.assertIs(
                    record["matches_pinned_windows_crlf_bytes"],
                    raw_match,
                )

    def test_upstream_gate_rejects_non_line_ending_changes(self) -> None:
        crlf_payload = b"GRID_PARAMETER_FILE reference.gpf\r\n-0.200\r\n"
        manifest = self._manifest(crlf_payload)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "example" / "reference.W.map"
            path.parent.mkdir()
            path.write_bytes(
                b"GRID_PARAMETER_FILE reference.gpf\n-0.201\n"
            )

            with self.assertRaises(VERIFY.HydratedAcceptanceError) as raised:
                VERIFY._verify_upstream(root, manifest)

        self.assertEqual(
            raised.exception.code,
            "HYDRATED_ACCEPTANCE_UPSTREAM_PORTABLE_IDENTITY_MISMATCH",
        )

    def test_upstream_gate_rejects_mixed_line_endings(self) -> None:
        crlf_payload = b"GRID_PARAMETER_FILE reference.gpf\r\n-0.200\r\n"
        manifest = self._manifest(crlf_payload)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "example" / "reference.W.map"
            path.parent.mkdir()
            path.write_bytes(
                b"GRID_PARAMETER_FILE reference.gpf\r\n-0.200\n"
            )

            with self.assertRaises(VERIFY.HydratedAcceptanceError) as raised:
                VERIFY._verify_upstream(root, manifest)

        self.assertEqual(
            raised.exception.code,
            "HYDRATED_ACCEPTANCE_UPSTREAM_LINE_ENDINGS_UNSUPPORTED",
        )

    def test_upstream_gate_does_not_inherit_parent_git_root(self) -> None:
        crlf_payload = b"GRID_PARAMETER_FILE reference.gpf\r\n-0.200\r\n"
        manifest = self._manifest(crlf_payload)
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            (parent / ".git").mkdir()
            root = parent / "detached-input"
            path = root / "example" / "reference.W.map"
            path.parent.mkdir(parents=True)
            path.write_bytes(crlf_payload)
            with mock.patch.object(
                VERIFY,
                "_run",
                side_effect=AssertionError("ancestor Git must not be probed"),
            ):
                _, evidence = VERIFY._verify_upstream(root, manifest)
        self.assertFalse(evidence["git_root_verified"])
        self.assertEqual(
            evidence["detected_commit"],
            "not_available_hashes_verified",
        )

    def test_manifest_is_strict_metadata_only_schema(self) -> None:
        manifest = VERIFY._load_manifest()
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(
            manifest["fixture_id"],
            "hydrated_1uw6_external",
        )
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
            self.assertRegex(record["git_blob_sha1"], r"^[0-9a-f]{40}$")
            self.assertRegex(record["sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(
                record["portable_text_identity"]["sha256"],
                r"^[0-9a-f]{64}$",
            )

    def test_manifest_rejects_contains_flag_or_unexpected_fixture_file(
        self,
    ) -> None:
        manifest = VERIFY._load_manifest()
        changed = json.loads(json.dumps(manifest))
        changed["contains_maps_or_outputs"] = True
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            manifest_path = fixture / "source_manifest.json"
            (fixture / "README.md").write_text(
                "metadata only",
                encoding="utf-8",
            )
            manifest_path.write_text(
                json.dumps(changed),
                encoding="utf-8",
            )
            with (
                mock.patch.object(VERIFY, "MANIFEST_PATH", manifest_path),
                self.assertRaises(VERIFY.HydratedAcceptanceError) as raised,
            ):
                VERIFY._load_manifest()
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
        )

    def test_manifest_pins_crystal_reference_and_pose_contract(self) -> None:
        manifest = VERIFY._load_manifest()
        reference = manifest["required_files"]["crystal_reference_ligand"]
        self.assertEqual(
            reference["path"],
            "example/hydrated_docking/data/1uw6_ligand.sdf",
        )
        self.assertEqual(
            reference["git_blob_sha1"],
            "16fa76ce32db813334007f0f994e903a560e8c24",
        )
        self.assertEqual(reference["size_bytes"], 1917)
        self.assertEqual(
            reference["sha256"],
            "c2010b6fd17e472601fb36fa1437a713ffbf351f9b84f103a12146e11209e2d9",
        )
        self.assertEqual(
            reference["portable_text_identity"],
            {
                "algorithm": VERIFY.PORTABLE_IDENTITY_ALGORITHM,
                "size_bytes": 1844,
                "sha256": (
                    "f9f9e492c8be271198ed27824c6e21aaa539c00b06612b204"
                    "d3a275058fff862"
                ),
            },
        )
        self.assertEqual(
            manifest["expected"]["pose_recovery"],
            VERIFY.PINNED_POSE_RECOVERY_CONTRACT,
        )

    def test_pose_contract_rejects_mapping_or_threshold_changes(self) -> None:
        for label, mutate in (
            (
                "mapping",
                lambda value: value[
                    "smiles_to_reference_sdf_atom_zero_based"
                ].__setitem__(0, 10),
            ),
            (
                "threshold",
                lambda value: value.__setitem__(
                    "maximum_rmsd_angstrom",
                    2.1,
                ),
            ),
            (
                "alignment",
                lambda value: value.__setitem__("alignment_applied", True),
            ),
        ):
            with self.subTest(label=label):
                changed = json.loads(
                    json.dumps(VERIFY.PINNED_POSE_RECOVERY_CONTRACT)
                )
                mutate(changed)
                with self.assertRaises(
                    VERIFY.HydratedAcceptanceError
                ) as raised:
                    VERIFY._validate_pose_recovery_contract(changed)
                self.assertEqual(
                    raised.exception.code,
                    "HYDRATED_ACCEPTANCE_POSE_CONTRACT_INVALID",
                )

    def test_reference_parser_rejects_unpinned_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reference.sdf"
            path.write_text(
                _synthetic_reference_sdf(),
                encoding="utf-8",
            )
            with self.assertRaises(
                VERIFY.HydratedAcceptanceError
            ) as raised:
                VERIFY._parse_crystal_reference_sdf(
                    path,
                    VERIFY.PINNED_POSE_RECOVERY_CONTRACT,
                )
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_ACCEPTANCE_REFERENCE_SDF_TOPOLOGY_MISMATCH",
        )
        self.assertNotEqual(
            raised.exception.details["expected_coordinate_sha256"],
            raised.exception.details["actual_coordinate_sha256"],
        )

    def test_pose_recovery_uses_same_frame_explicit_mapping(self) -> None:
        reference, models = _synthetic_pose_reference_and_models()
        evidence = VERIFY._evaluate_pose_recovery(
            models,
            reference,
            VERIFY.PINNED_POSE_RECOVERY_CONTRACT,
        )
        self.assertTrue(evidence["passed"])
        self.assertFalse(evidence["alignment_applied"])
        self.assertEqual(evidence["top_n_modes"], 3)
        self.assertEqual(
            [record["rmsd_angstrom"] for record in evidence["mode_rmsd_angstrom"]],
            [0.0, 0.0, 0.0],
        )

    def test_pose_recovery_rejects_output_coordinate_tamper(self) -> None:
        reference, models = _synthetic_pose_reference_and_models(
            coordinate_shift=3.0,
        )
        with self.assertRaises(VERIFY.HydratedAcceptanceError) as raised:
            VERIFY._evaluate_pose_recovery(
                models,
                reference,
                VERIFY.PINNED_POSE_RECOVERY_CONTRACT,
            )
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_ACCEPTANCE_POSE_RECOVERY_FAILED",
        )
        self.assertFalse(raised.exception.details["passed"])
        self.assertEqual(
            raised.exception.details["best_rmsd_angstrom_within_top_n"],
            3.0,
        )

    def test_pose_recovery_rejects_smiles_or_idx_tamper(self) -> None:
        cases = (
            (
                "smiles",
                1,
                "REMARK SMILES C[NH+]1CCCC1c1cccnc1",
                "HYDRATED_ACCEPTANCE_PDBQT_SMILES_MISMATCH",
            ),
            (
                "idx",
                2,
                (
                    "REMARK SMILES IDX 5 1 "
                    + " ".join(
                        str(value)
                        for value in VERIFY.PINNED_POSE_RECOVERY_CONTRACT[
                            "pdbqt_smiles_idx_pairs_one_based"
                        ][2:]
                    )
                ),
                "HYDRATED_ACCEPTANCE_PDBQT_MAPPING_MISMATCH",
            ),
        )
        for label, line_index, replacement, expected_code in cases:
            with self.subTest(label=label):
                reference, models = _synthetic_pose_reference_and_models()
                models[0][line_index] = replacement
                with self.assertRaises(
                    VERIFY.HydratedAcceptanceError
                ) as raised:
                    VERIFY._evaluate_pose_recovery(
                        models,
                        reference,
                        VERIFY.PINNED_POSE_RECOVERY_CONTRACT,
                    )
                self.assertEqual(raised.exception.code, expected_code)

    def test_mode_gate_rejects_7_accepts_8_and_9_and_rejects_10(
        self,
    ) -> None:
        expected = {
            "accepted_output_mode_counts": [8, 9],
            "minimum_output_modes": 8,
            "maximum_output_modes": 9,
            "num_modes": 9,
        }
        for count, accepted in ((7, False), (8, True), (9, True), (10, False)):
            models = [[f"MODEL {index}"] for index in range(1, count + 1)]
            with self.subTest(count=count):
                if accepted:
                    evidence = VERIFY._validate_output_mode_contract(
                        models,
                        expected,
                    )
                    self.assertEqual(evidence["actual"], count)
                    self.assertTrue(evidence["continuous"])
                else:
                    with self.assertRaises(
                        VERIFY.HydratedAcceptanceError
                    ) as raised:
                        VERIFY._validate_output_mode_contract(
                            models,
                            expected,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "HYDRATED_ACCEPTANCE_VINA_MODE_COUNT",
                    )

    def test_mode_gate_requires_continuous_model_numbers(self) -> None:
        expected = {
            "accepted_output_mode_counts": [8, 9],
            "minimum_output_modes": 8,
            "maximum_output_modes": 9,
            "num_modes": 9,
        }
        models = [[f"MODEL {index}"] for index in (1, 2, 3, 4, 6, 7, 8, 9)]
        with self.assertRaises(VERIFY.HydratedAcceptanceError) as raised:
            VERIFY._validate_output_mode_contract(models, expected)
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_ACCEPTANCE_VINA_MODE_SEQUENCE",
        )

    def test_tool_gate_rejects_python_autogrid_and_vina_substitution(
        self,
    ) -> None:
        expected = {
            "meeko": {"validated_version": "0.7.1"},
            "vina": {"validated_version": "1.2.7"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            supplied_python = root / "python.exe"
            supplied_autogrid = root / "autogrid4.exe"
            supplied_vina = root / "vina.exe"
            substituted = root / "substituted.exe"
            for path in (
                supplied_python,
                supplied_autogrid,
                supplied_vina,
                substituted,
            ):
                path.write_bytes(b"synthetic executable")

            valid_meeko = _tool_result(
                supplied_python,
                version="0.7.1",
            )
            valid_autogrid = _tool_result(
                supplied_autogrid,
                version="4.2.7",
            )
            valid_vina = _tool_result(
                supplied_vina,
                version="1.2.7",
                capabilities={
                    "features": {
                        "maps": {
                            "status": "supported",
                            "supported": True,
                        }
                    }
                },
            )
            cases = (
                (
                    "python",
                    _tool_result(substituted, version="0.7.1"),
                    valid_autogrid,
                    valid_vina,
                    "HYDRATED_ACCEPTANCE_MEEKO_PYTHON_PATH_MISMATCH",
                ),
                (
                    "autogrid",
                    valid_meeko,
                    _tool_result(substituted, version="4.2.7"),
                    valid_vina,
                    "HYDRATED_ACCEPTANCE_AUTOGRID_PATH_MISMATCH",
                ),
                (
                    "vina",
                    valid_meeko,
                    valid_autogrid,
                    _tool_result(
                        substituted,
                        version="1.2.7",
                        source="bundled",
                        is_bundled=True,
                        capabilities={
                            "features": {
                                "maps": {
                                    "status": "supported",
                                    "supported": True,
                                }
                            }
                        },
                    ),
                    "HYDRATED_ACCEPTANCE_VINA_PATH_MISMATCH",
                ),
            )
            for (
                label,
                meeko_result,
                autogrid_result,
                vina_result,
                expected_code,
            ) in cases:
                with (
                    self.subTest(label=label),
                    mock.patch.object(
                        VERIFY.meeko_adapter,
                        "detect",
                        return_value=meeko_result,
                    ),
                    mock.patch.object(
                        VERIFY.autogrid_adapter,
                        "detect",
                        return_value=autogrid_result,
                    ),
                    mock.patch.object(
                        VERIFY.vina_adapter,
                        "detect",
                        return_value=vina_result,
                    ) as vina_detect,
                    self.assertRaises(
                        VERIFY.HydratedAcceptanceError
                    ) as raised,
                ):
                    VERIFY._verify_configured_tools(
                        supplied_python,
                        supplied_vina,
                        supplied_autogrid,
                        expected,
                    )
                self.assertEqual(raised.exception.code, expected_code)
                if label == "vina":
                    args, kwargs = vina_detect.call_args
                    self.assertEqual(
                        VERIFY._normalized_path(args[0]),
                        VERIFY._normalized_path(supplied_vina),
                    )
                    self.assertIn("bundled_path", kwargs)
                    self.assertNotEqual(
                        VERIFY._normalized_path(kwargs["bundled_path"]),
                        VERIFY._normalized_path(supplied_vina),
                    )

    def test_best_water_map_numeric_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oa_map = root / "receptor.OA.map"
            hd_map = root / "receptor.HD.map"
            official = root / "receptor.W.official.map"
            generated = root / "receptor.W.generated.map"
            oa_values = [-1.0, 1.0, *([-1.0] * 25)]
            hd_values = [-2.0, -3.0, *([-2.0] * 25)]
            expected_values = [-1.2, -0.2, *([-1.2] * 25)]
            oa_map.write_text(
                _map_payload(oa_values),
                encoding="ascii",
            )
            hd_map.write_text(
                _map_payload(hd_values),
                encoding="ascii",
            )
            official.write_text(
                _map_payload(expected_values),
                encoding="ascii",
            )
            evidence = VERIFY._verify_water_map(
                oa_map,
                hd_map,
                official,
                generated,
                {"value_count": 27},
            )
            parsed = VERIFY.parse_autogrid_map(generated)
        self.assertEqual(list(parsed.values), expected_values)
        self.assertEqual(evidence["value_count"], 27)
        self.assertTrue(evidence["semantic_identity"])

    def test_clean_room_postprocess_oracle_is_8_1_9(self) -> None:
        retained = [1, 1, 0, 1, 1, 1, 1, 2, 1]
        summary = {
            "raw_water_count": 18,
            "strong_water_count": 8,
            "weak_water_count": 1,
            "displaced_water_count": 9,
        }
        modes = [
            {"strong_water_count": value, "weak_water_count": 0}
            for value in retained
        ]
        actual = VERIFY._clean_room_postprocess_counts(summary, modes)
        expected = {**summary, "retained_by_mode": retained}
        accepted = VERIFY._validate_clean_room_postprocess_oracle(
            actual,
            expected,
        )
        self.assertEqual(accepted["strong_water_count"], 8)
        self.assertEqual(accepted["weak_water_count"], 1)
        self.assertEqual(accepted["displaced_water_count"], 9)

        changed = {**expected, "weak_water_count": 2}
        with self.assertRaises(VERIFY.HydratedAcceptanceError) as raised:
            VERIFY._validate_clean_room_postprocess_oracle(
                actual,
                changed,
            )
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_ACCEPTANCE_POSTPROCESS_MISMATCH",
        )

    def test_report_semantic_oracle_requires_existing_statements(self) -> None:
        project_expected = VERIFY._load_manifest()["expected"]["project_api"]
        phrases = project_expected["report_required_phrases"]
        report = "\n\n".join(phrases)
        evidence = VERIFY._validate_report_semantics(
            report,
            project_expected,
        )
        self.assertTrue(evidence["all_present"])

        with self.assertRaises(VERIFY.HydratedAcceptanceError) as raised:
            VERIFY._validate_report_semantics(
                "\n\n".join(phrases[:-1]),
                project_expected,
            )
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_ACCEPTANCE_PROJECT_REPORT_CONTENT_MISSING",
        )

    def test_success_orchestration_records_paths_and_cleans_up(self) -> None:
        captured_root: list[Path] = []
        manifest = {
            "fixture_id": "hydrated_1uw6_external",
            "distribution": "metadata_only",
            "expected": {},
        }

        def project_setup(
            work_root: Path,
            _files: object,
            _expected: object,
        ) -> dict[str, object]:
            captured_root.append(work_root)
            project = work_root / "project"
            project.mkdir()
            return {"project_dir": str(project)}

        with tempfile.TemporaryDirectory() as upstream:
            with (
                mock.patch.object(
                    VERIFY,
                    "_load_manifest",
                    return_value=manifest,
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_upstream",
                    return_value=(
                        {
                            key: SCRIPT_PATH
                            for key in VERIFY.MANIFEST_REQUIRED_FILES
                        },
                        {"identity": "synthetic"},
                    ),
                ),
                mock.patch.object(
                    VERIFY,
                    "save_settings",
                    return_value=None,
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_configured_tools",
                    return_value={"tools": "synthetic"},
                ),
                mock.patch.object(
                    VERIFY,
                    "_create_and_configure_project",
                    side_effect=project_setup,
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_project_hydrated_ligand",
                    return_value={"prepared": True},
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_project_maps",
                    return_value={"maps": True},
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_project_run",
                    return_value={"run_id": "run_001"},
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_project_results_and_report",
                    return_value={"report": True},
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_postprocess",
                    return_value={"postprocess": True},
                ),
                mock.patch.dict(
                    os.environ,
                    {
                        VERIFY.SETTINGS_ENV_VAR: "original-settings.json",
                        VERIFY.RESOURCE_DIR_ENV_VAR: "original-resources",
                    },
                    clear=False,
                ),
            ):
                result = VERIFY.verify_hydrated_1uw6(
                    upstream,
                    autogrid_executable=SCRIPT_PATH,
                    python_executable=SCRIPT_PATH,
                    vina_executable=SCRIPT_PATH,
                )
                self.assertEqual(
                    os.environ[VERIFY.SETTINGS_ENV_VAR],
                    "original-settings.json",
                )
                self.assertEqual(
                    os.environ[VERIFY.RESOURCE_DIR_ENV_VAR],
                    "original-resources",
                )

        self.assertTrue(result["ok"])
        self.assertEqual(len(captured_root), 1)
        self.assertFalse(captured_root[0].exists())
        self.assertTrue(result["temporary_cleanup"]["removed"])
        self.assertTrue(result["settings_environment"]["restored"])
        self.assertTrue(
            result["toolchain_resource_environment"]["restored"]
        )
        self.assertEqual(
            set(result["steps"]),
            {
                "upstream_identity",
                "local_inputs_and_toolchain",
                "project_create_import_and_parameters",
                "project_hydrated_ligand_preparation",
                "project_gpf_autogrid_base_and_water_maps",
                "project_vina_normalization_and_hydrated_postprocess",
                "project_scores_and_markdown_report",
                "official_retained_water_reference",
            },
        )

    def test_failure_restores_settings_and_removes_temporary_project(
        self,
    ) -> None:
        configured_error = VERIFY.HydratedAcceptanceError(
            "TEST_TOOL_FAILURE",
            "synthetic configured-tool failure",
        )
        with tempfile.TemporaryDirectory() as upstream:
            with (
                mock.patch.object(
                    VERIFY,
                    "_verify_upstream",
                    return_value=({}, {"portable_content": "verified"}),
                ),
                mock.patch.object(
                    VERIFY,
                    "_verify_configured_tools",
                    side_effect=configured_error,
                ),
                mock.patch.dict(
                    os.environ,
                    {VERIFY.SETTINGS_ENV_VAR: "original-settings.json"},
                    clear=False,
                ),
            ):
                with self.assertRaises(
                    VERIFY.HydratedAcceptanceError
                ) as raised:
                    VERIFY.verify_hydrated_1uw6(
                        upstream,
                        autogrid_executable=SCRIPT_PATH,
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
        self.assertTrue(
            raised.exception.details["settings_environment"]["restored"]
        )
        self.assertTrue(
            raised.exception.details[
                "toolchain_resource_environment"
            ]["restored"]
        )
        self.assertEqual(
            raised.exception.steps["local_inputs_and_toolchain"]["error"][
                "code"
            ],
            "TEST_TOOL_FAILURE",
        )


if __name__ == "__main__":
    unittest.main()
