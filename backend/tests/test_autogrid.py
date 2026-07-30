from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.autogrid import (  # noqa: E402
    canonical_atom_type,
    canonical_atom_types,
    compute_ad4zn_box_coverage,
    compute_requested_box_grid_coverage,
    generate_maps,
    get_maps_defaults,
    get_maps_status,
    import_maps,
    set_scoring_protocol,
    validate_active_maps,
)
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    create_project,
    execute_prepared_vina_run,
    generate_vina_config,
    get_project_workflow_status,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    prepare_vina_run,
    update_box_params,
    update_vina_run_protocol,
)


RECEPTOR_PDBQT = (
    "ATOM      1  C   REC A   1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
    "ATOM      2  O   REC A   1       1.200   0.000   0.000  1.00  0.00    -0.200 OA\n"
)
LIGAND_PDBQT = (
    "ROOT\n"
    "ATOM      1  C   LIG     1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
    "ATOM      2  N   LIG     1       1.200   0.000   0.000  1.00  0.00    -0.100 NA\n"
    "ENDROOT\n"
    "TORSDOF 0\n"
)


class AutoGridWorkflowTests(unittest.TestCase):
    def test_atom_type_canonicalization_preserves_unknowns_for_validation(
        self,
    ) -> None:
        self.assertEqual(canonical_atom_type("CL"), "Cl")
        self.assertEqual(canonical_atom_type("br"), "Br")
        self.assertEqual(canonical_atom_type("SI"), "Si")
        self.assertEqual(canonical_atom_type("W"), "W")
        self.assertEqual(canonical_atom_type("w"), "W")
        self.assertEqual(canonical_atom_type("Xx"), "Xx")
        self.assertEqual(
            canonical_atom_types(["CL", "br", "SI", "W", "Cl"]),
            ["Br", "Cl", "Si", "W"],
        )

    def test_requested_box_grid_coverage_uses_closed_intervals_and_tolerance(
        self,
    ) -> None:
        box = {
            "center_x": 1.0,
            "center_y": -2.0,
            "center_z": 3.0,
            "size_x": 47.25,
            "size_y": 15.0,
            "size_z": 15.0,
        }

        boundary = compute_requested_box_grid_coverage(
            box,
            [126, 40, 40],
            0.375,
        )

        self.assertTrue(boundary["ok"], boundary)
        coverage = boundary["coverage"]
        self.assertTrue(coverage["covers_requested_box"])
        self.assertEqual(coverage["interval_semantics"], "closed")
        self.assertEqual(coverage["tolerance_angstrom"], 1e-6)
        self.assertEqual(
            coverage["requested_box_bounds_angstrom"]["min"]["x"],
            -22.625,
        )
        self.assertEqual(
            coverage["effective_grid_bounds_angstrom"]["max"]["x"],
            24.625,
        )
        self.assertEqual(
            coverage["axis_coverage"]["x"]["minimum_margin_angstrom"],
            0.0,
        )
        self.assertRegex(boundary["coverage_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            boundary["canonical_sha256"],
            boundary["coverage_sha256"],
        )

        under = compute_requested_box_grid_coverage(
            box,
            [126, 38, 40],
            0.375,
        )
        self.assertTrue(under["ok"], under)
        self.assertFalse(under["coverage"]["covers_requested_box"])
        self.assertFalse(
            under["coverage"]["axis_coverage"]["y"][
                "covers_requested_interval"
            ]
        )

        tolerated = compute_requested_box_grid_coverage(
            {
                **box,
                "size_x": 47.2500015,
            },
            [126, 40, 40],
            0.375,
        )
        self.assertTrue(tolerated["ok"], tolerated)
        self.assertTrue(tolerated["coverage"]["covers_requested_box"])

    def test_requested_box_grid_coverage_rejects_invalid_geometry(self) -> None:
        box = {
            "center_x": 0,
            "center_y": 0,
            "center_z": 0,
            "size_x": 8,
            "size_y": 8,
            "size_z": 8,
        }
        for points, spacing in (
            ([3, 8, 8], 1.0),
            ([8, 8, 8], 0.0),
            ([float("inf"), 8, 8], 1.0),
        ):
            with self.subTest(points=points, spacing=spacing):
                result = compute_requested_box_grid_coverage(
                    box,
                    points,
                    spacing,
                )
                self.assertFalse(result["ok"], result)
                self.assertEqual(
                    result["error"]["code"],
                    "MAPS_GRID_COVERAGE_GEOMETRY_INVALID",
                )

    def _create_project(self, temp_dir: str, *, ligand_text: str = LIGAND_PDBQT) -> Path:
        created = create_project("ad4_demo", temp_dir)
        self.assertTrue(created["ok"])
        project_dir = Path(created["project_dir"])
        receptor = Path(temp_dir) / "receptor.pdbqt"
        ligand = Path(temp_dir) / "ligand.pdbqt"
        receptor.write_text(RECEPTOR_PDBQT, encoding="utf-8")
        ligand.write_text(ligand_text, encoding="utf-8")
        self.assertTrue(import_receptor_pdbqt(str(project_dir), str(receptor))["ok"])
        self.assertTrue(import_ligand_pdbqt(str(project_dir), str(ligand))["ok"])
        self.assertTrue(
            update_box_params(
                str(project_dir),
                {
                    "center_x": 1,
                    "center_y": 2,
                    "center_z": 3,
                    "size_x": 20,
                    "size_y": 21,
                    "size_z": 22,
                },
            )["ok"]
        )
        return project_dir

    def _autogrid_ok(
        self,
        executable: Path,
        *,
        version: str = "4.2.6",
    ) -> ToolCheckResult:
        return ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="ok",
            version=version,
            path=str(executable),
            message="已检测到 AutoGrid4。",
            source="configured",
        )

    def _vina_ok(self, executable: Path) -> ToolCheckResult:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version="1.2.7",
            path=str(executable),
            message="已检测到 AutoDock Vina。",
            source="configured",
        )

    @staticmethod
    def _fake_autogrid(
        _executable: str,
        gpf_file: str,
        log_file: str,
        working_directory: str | Path,
        **_kwargs: object,
    ) -> dict[str, object]:
        root = Path(working_directory)
        gpf_text = (root / gpf_file).read_text(encoding="utf-8")
        ligand_types = next(
            line.split()[1:]
            for line in gpf_text.splitlines()
            if line.startswith("ligand_types ")
        )
        names = [
            "receptor.maps.fld",
            *(f"receptor.{atom_type}.map" for atom_type in ligand_types),
            "receptor.e.map",
            "receptor.d.map",
        ]
        for name in names:
            (root / name).write_text(f"mock {name}\n", encoding="utf-8")
        (root / log_file).write_text("Successful Completion\n", encoding="utf-8")
        return {
            "ok": True,
            "command": ["autogrid4", "-p", gpf_file, "-l", log_file],
            "exit_code": 0,
            "stdout": "AutoGrid complete\n",
            "stderr": "",
            "error": "",
        }

    def _generate(self, project_dir: Path, temp_dir: str) -> dict[str, object]:
        executable = Path(temp_dir) / "autogrid4.exe"
        executable.write_bytes(b"mock autogrid")
        with patch(
            "dockstart_core.autogrid.autogrid_adapter.detect",
            return_value=self._autogrid_ok(executable),
        ):
            return generate_maps(str(project_dir), runner=self._fake_autogrid)

    def test_generates_gpf_manifest_and_validated_maps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            defaults = get_maps_defaults(str(project_dir))
            self.assertTrue(defaults["ok"])
            self.assertEqual(defaults["defaults"]["spacing"], 0.375)
            self.assertEqual(defaults["defaults"]["grid_points"], {"x": 54, "y": 56, "z": 60})

            generated = self._generate(project_dir, temp_dir)
            self.assertTrue(generated["ok"])
            manifest = generated["manifest"]
            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(manifest["maps"]["ligand_atom_types"], ["C", "NA"])
            self.assertTrue((project_dir / generated["manifest_file"]).is_file())
            gpf = project_dir / "maps" / generated["map_set_id"] / "receptor.gpf"
            gpf_text = gpf.read_text(encoding="utf-8")
            self.assertIn("npts 54 56 60", gpf_text)
            self.assertIn("ligand_types C NA", gpf_text)

            status = validate_active_maps(str(project_dir))
            self.assertTrue(status["ok"])
            self.assertTrue(status["ready"])
            self.assertTrue(status["protocol_active"])

    def test_ad4zn_box_coverage_uses_closed_intervals_and_exact_markers(
        self,
    ) -> None:
        receptor_text = (
            "HETATM    1 ZN    ZN A 500       0.000   0.000   0.000"
            "  1.00  0.00     0.000 ZN\n"
            "HETATM    2 TZ    ZN A 500      -1.000  -1.000  -1.000"
            "  1.00  0.00     0.000 TZ\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            receptor = Path(temp_dir) / "receptor_tz.pdbqt"
            receptor.write_text(receptor_text, encoding="utf-8")
            box = {
                "center_x": -1,
                "center_y": -1,
                "center_z": -1,
                "size_x": 2,
                "size_y": 2,
                "size_z": 2,
            }

            result = compute_ad4zn_box_coverage(
                receptor,
                box,
                [2, 2, 2],
                1.0,
            )

            self.assertTrue(result["ok"], result)
            coverage = result["box_coverage"]
            self.assertTrue(
                coverage["all_zn_tz_inside_requested_box"]
            )
            self.assertTrue(
                coverage["all_zn_tz_inside_effective_grid"]
            )
            self.assertEqual(
                coverage["markers"]["ZN"][
                    "requested_box_margin_angstrom"
                ],
                {"x": 0.0, "y": 0.0, "z": 0.0},
            )
            self.assertEqual(
                coverage["markers"]["ZN"][
                    "effective_grid_margin_angstrom"
                ],
                {"x": 0.0, "y": 0.0, "z": 0.0},
            )

            receptor.write_text(
                receptor_text.replace(
                    "       0.000   0.000   0.000",
                    "       0.001   0.000   0.000",
                    1,
                ),
                encoding="utf-8",
            )
            just_outside = compute_ad4zn_box_coverage(
                receptor,
                box,
                [2, 2, 2],
                1.0,
            )
            self.assertTrue(just_outside["ok"], just_outside)
            self.assertFalse(
                just_outside["box_coverage"][
                    "all_zn_tz_inside_requested_box"
                ]
            )
            self.assertFalse(
                just_outside["box_coverage"][
                    "all_zn_tz_inside_effective_grid"
                ]
            )

            receptor.write_text(
                receptor_text
                + (
                    "HETATM    3 TZ    ZN A 500      -0.500  -0.500"
                    "  -0.500  1.00  0.00     0.000 TZ\n"
                ),
                encoding="utf-8",
            )
            duplicate = compute_ad4zn_box_coverage(
                receptor,
                box,
                [2, 2, 2],
                1.0,
            )
            self.assertFalse(duplicate["ok"], duplicate)
            self.assertEqual(
                duplicate["error"]["code"],
                "AD4ZN_ZN_TZ_COUNT_INVALID",
            )

    def test_ad4zn_parameter_snapshot_race_fails_before_runner_and_is_audited(
        self,
    ) -> None:
        parameter_text = "\n".join(
            (
                "# GNU General Public License, version 2 or later",
                "FE_coeff_vdW 0.1662",
                "FE_coeff_hbond 0.1209",
                "FE_coeff_estat 0.1406",
                "FE_coeff_desolv 0.1322",
                "FE_coeff_tors 0.2983",
                "atom_par C 4.00 0.150 33.5103 -0.00143 0.0 0.0 0 -1 -1 0",
                "atom_par NA 3.50 0.160 22.4493 -0.00162 1.9 5.0 4 -1 -1 1",
                "atom_par ZN 1.48 0.550 1.7000 -0.00110 0.0 0.0 0 -1 -1 4",
                "atom_par TZ 1.00 0.000 0.0000 0.00000 0.0 0.0 0 -1 -1 0",
                "",
            )
        )
        receptor_tz_text = (
            "HETATM    1 ZN    ZN A 500       0.000   0.000   0.000"
            "  1.00  0.00     0.000 ZN\n"
            "HETATM    2 TZ    ZN A 500       1.000   1.000   1.000"
            "  1.00  0.00     0.000 TZ\n"
        )
        parameter_bytes = parameter_text.encode("utf-8")
        parameter_sha256 = hashlib.sha256(parameter_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self.assertTrue(
                set_scoring_protocol(
                    str(project_dir),
                    "ad4zn_beta",
                )["ok"]
            )
            prepared_receptor = (
                project_dir
                / "ad4zn"
                / "prepared"
                / "receptor_tz.pdbqt"
            )
            parameter_file = (
                project_dir
                / "ad4zn"
                / "parameters"
                / "AD4Zn.dat"
            )
            prepared_receptor.parent.mkdir(parents=True)
            parameter_file.parent.mkdir(parents=True)
            prepared_receptor.write_text(
                receptor_tz_text,
                encoding="utf-8",
            )
            parameter_file.write_bytes(parameter_bytes)
            executable = Path(temp_dir) / "autogrid4.exe"
            executable.write_bytes(b"mock autogrid 4.2.7")
            ad4zn_status = {
                "ok": True,
                "preparation_ready": True,
                "prepared_receptor": {
                    "relative_path": (
                        "ad4zn/prepared/receptor_tz.pdbqt"
                    ),
                },
                "parameter_file": {
                    "relative_path": "ad4zn/parameters/AD4Zn.dat",
                    "size_bytes": len(parameter_bytes),
                    "sha256": parameter_sha256,
                    "atom_types": ["C", "NA", "TZ", "ZN"],
                },
                "selected_site": {},
                "review": {},
            }
            real_copyfile = shutil.copyfile

            def racing_copyfile(
                source: str | Path,
                target: str | Path,
                *_args: object,
                **_kwargs: object,
            ) -> str:
                copied = real_copyfile(source, target)
                target_path = Path(target)
                if target_path.name == "AD4Zn.dat":
                    target_path.write_text(
                        parameter_text.replace(
                            "FE_coeff_vdW 0.1662",
                            "FE_coeff_vdW 0.2662",
                        ),
                        encoding="utf-8",
                    )
                return copied

            runner = Mock(side_effect=self._fake_autogrid)
            with (
                patch(
                    "dockstart_core.autogrid.autogrid_adapter.detect",
                    return_value=self._autogrid_ok(
                        executable,
                        version="4.2.7",
                    ),
                ),
                patch(
                    "dockstart_core.ad4zn.get_status",
                    return_value=ad4zn_status,
                ),
                patch(
                    "dockstart_core.ad4zn.SUPPORTED_PARAMETER_REFERENCE_SHA256",
                    parameter_sha256,
                ),
                patch(
                    "dockstart_core.autogrid.shutil.copyfile",
                    side_effect=racing_copyfile,
                ),
            ):
                result = generate_maps(
                    str(project_dir),
                    runner=runner,
                )

            self.assertFalse(result["ok"], result)
            self.assertEqual(
                result["error"]["code"],
                "AD4ZN_PARAMETER_CHANGED_DURING_MAPS_PREPARATION",
            )
            runner.assert_not_called()
            manifest_path = project_dir / result["manifest_file"]
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(
                manifest["error"]["code"],
                "AD4ZN_PARAMETER_CHANGED_DURING_MAPS_PREPARATION",
            )
            self.assertEqual(
                manifest["parameter_file"]["sha256"],
                hashlib.sha256(
                    (
                        manifest_path.parent
                        / "inputs"
                        / "AD4Zn.dat"
                    ).read_bytes()
                ).hexdigest(),
            )
            self.assertNotEqual(
                manifest["parameter_file"]["sha256"],
                parameter_sha256,
            )
            project_payload = json.loads(
                (project_dir / "project.json").read_text(encoding="utf-8")
            )
            self.assertFalse(
                str(
                    (
                        project_payload.get("ad4zn")
                        if isinstance(
                            project_payload.get("ad4zn"),
                            dict,
                        )
                        else {}
                    ).get("active_manifest")
                    or ""
                )
            )

    def test_standard_generation_rejects_old_or_unknown_autogrid_version(
        self,
    ) -> None:
        for version in ("4.2.5", "unknown"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp_dir:
                project_dir = self._create_project(temp_dir)
                executable = Path(temp_dir) / "autogrid4.exe"
                executable.write_bytes(b"mock autogrid")
                runner = Mock(side_effect=self._fake_autogrid)
                with patch(
                    "dockstart_core.autogrid.autogrid_adapter.detect",
                    return_value=self._autogrid_ok(
                        executable,
                        version=version,
                    ),
                ):
                    result = generate_maps(
                        str(project_dir),
                        runner=runner,
                    )

                self.assertFalse(result["ok"], result)
                self.assertEqual(
                    result["error"]["code"],
                    "AUTOGRID_VERSION_UNSUPPORTED",
                )
                self.assertIn("required>=4.2.6", result["error"]["raw_error"])
                runner.assert_not_called()

    def test_generated_manifest_version_gate_reaches_status_and_run_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            generated = self._generate(project_dir, temp_dir)
            self.assertTrue(generated["ok"], generated)
            self.assertTrue(generate_vina_config(str(project_dir))["ok"])
            manifest_path = project_dir / generated["manifest_file"]
            original_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            vina = Path(temp_dir) / "vina.exe"
            vina.write_bytes(b"mock vina")

            for version in ("4.2.5", "unknown"):
                with self.subTest(version=version):
                    manifest = json.loads(json.dumps(original_manifest))
                    manifest["autogrid"]["version"] = version
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

                    status = validate_active_maps(str(project_dir))
                    self.assertFalse(status["ok"], status)
                    self.assertIn("AutoGrid 4.2.6+", "；".join(status["issues"]))
                    with patch(
                        "dockstart_core.project.vina_adapter.detect",
                        return_value=self._vina_ok(vina),
                    ):
                        prepared = prepare_vina_run(str(project_dir))
                    self.assertFalse(prepared["ok"], prepared)
                    self.assertEqual(
                        prepared["error"]["code"],
                        "MAPS_VALIDATION_FAILED",
                    )
                    self.assertIn(
                        "AutoGrid 4.2.6+",
                        prepared["error"]["raw_error"],
                    )

    def test_published_generated_maps_remain_ready_without_live_autogrid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            generated = self._generate(project_dir, temp_dir)
            self.assertTrue(generated["ok"], generated)

            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=ToolCheckResult(
                    key="autogrid4",
                    name="AutoGrid4",
                    status="missing",
                    message="未检测到 AutoGrid4。",
                    source="missing",
                ),
            ):
                status = get_maps_status(str(project_dir))

            self.assertTrue(status["ok"], status)
            self.assertTrue(status["ready"], status)
            self.assertEqual(status["tool"]["status"], "missing")

    def test_ad4_protocol_forces_evaluation_autobox_off(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            evaluation = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                True,
            )
            self.assertTrue(evaluation["ok"], evaluation)
            self.assertTrue(
                evaluation["project"]["docking_protocol"]["autobox"]
            )

            switched = set_scoring_protocol(str(project_dir), "ad4_maps")

            self.assertTrue(switched["ok"], switched)
            self.assertFalse(
                switched["project"]["docking_protocol"]["autobox"]
            )
            workflow = get_project_workflow_status(str(project_dir))
            self.assertFalse(workflow["project"]["docking_protocol"]["autobox"])

            project_json = project_dir / "project.json"
            payload = json.loads(project_json.read_text(encoding="utf-8"))
            payload["docking_protocol"]["autobox"] = True
            payload["box"]["size_x"] = 0
            project_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            defended = get_project_workflow_status(str(project_dir))
            self.assertIn(
                "合法的 docking box",
                defended["next_recommended_action"],
            )

    def test_receptor_change_invalidates_active_maps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            generated = self._generate(project_dir, temp_dir)
            self.assertTrue(generated["ok"])
            receptor = project_dir / "prepared" / "receptor.pdbqt"
            receptor.write_text(RECEPTOR_PDBQT + "REMARK changed\n", encoding="utf-8")

            status = validate_active_maps(str(project_dir))
            self.assertFalse(status["ok"])
            self.assertIn("受体 SHA256", "；".join(status["issues"]))

    def test_standard_maps_protocol_blocks_metal_atom_types(self) -> None:
        metal_ligand = LIGAND_PDBQT.replace("     0.000 C\n", "     0.000 Zn\n", 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir, ligand_text=metal_ligand)
            defaults = get_maps_defaults(str(project_dir))
            self.assertFalse(defaults["ok"])
            self.assertEqual(defaults["error"]["code"], "MAPS_METAL_PROTOCOL_REQUIRED")

    def test_import_uses_actual_pdbqt_types_when_gpf_declares_metal_superset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source_dir = Path(temp_dir) / "external_maps"
            source_dir.mkdir()
            receptor_name = "source_receptor.pdbqt"
            (source_dir / receptor_name).write_bytes(
                (project_dir / "prepared" / "receptor.pdbqt").read_bytes()
            )
            prefix = "source"
            (source_dir / f"{prefix}.gpf").write_text(
                "\n".join(
                    (
                        "npts 54 56 60",
                        f"gridfld {prefix}.maps.fld",
                        "spacing 0.375",
                        "receptor_types A C HD N NA OA SA Zn",
                        "ligand_types C NA",
                        f"receptor {receptor_name}",
                        f"gridcenter 1 2 3",
                        f"map {prefix}.C.map",
                        f"map {prefix}.NA.map",
                        f"elecmap {prefix}.e.map",
                        f"dsolvmap {prefix}.d.map",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            for name in (
                f"{prefix}.maps.fld",
                f"{prefix}.C.map",
                f"{prefix}.NA.map",
                f"{prefix}.e.map",
                f"{prefix}.d.map",
            ):
                (source_dir / name).write_text(f"fixture {name}\n", encoding="utf-8")

            imported = import_maps(str(project_dir), str(source_dir / f"{prefix}.maps.fld"))

            self.assertTrue(imported["ok"], imported.get("error"))
            receptor = imported["manifest"]["receptor"]
            self.assertEqual(receptor["atom_types"], ["C", "OA"])
            self.assertIn("Zn", receptor["gpf_declared_atom_types"])

    def test_prepared_ad4_run_uses_immutable_maps_and_separate_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            generated = self._generate(project_dir, temp_dir)
            self.assertTrue(generated["ok"])
            self.assertTrue(generate_vina_config(str(project_dir))["ok"])
            vina = Path(temp_dir) / "vina.exe"
            vina.write_bytes(b"mock vina")
            with patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok(vina)):
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"])
            metadata = prepared["metadata"]
            self.assertEqual(metadata["scoring_protocol"], "ad4_maps")
            self.assertEqual(metadata["scoring_function"], "ad4")
            self.assertIn("--maps", metadata["command"])
            self.assertEqual(metadata["command"][metadata["command"].index("--scoring") + 1], "ad4")
            config_text = (project_dir / metadata["config_snapshot"]).read_text(encoding="utf-8")
            self.assertIn("scoring = ad4", config_text)
            self.assertNotIn("receptor =", config_text)

            map_snapshot = metadata["ad4_maps"]["files"][0]
            (project_dir / map_snapshot["relative_path"]).write_text("tampered\n", encoding="utf-8")
            rejected = execute_prepared_vina_run(str(project_dir), prepared["run_id"])
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["error"]["code"], "RUN_AD4_MAP_HASH_MISMATCH")

    def test_status_without_maps_is_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=ToolCheckResult(
                    key="autogrid4",
                    name="AutoGrid4",
                    status="missing",
                    message="未检测到 AutoGrid4。",
                    source="missing",
                ),
            ):
                status = get_maps_status(str(project_dir))
            self.assertTrue(status["ok"])
            self.assertFalse(status["ready"])
            self.assertEqual(status["tool"]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
