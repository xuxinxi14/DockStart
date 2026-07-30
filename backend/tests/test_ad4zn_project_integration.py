from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import ad4zn as ad4zn_module  # noqa: E402
from dockstart_core.ad4zn import (  # noqa: E402
    REQUIRED_CONFIRMATIONS,
    get_status as get_ad4zn_status,
    prepare_receptor as prepare_ad4zn_receptor,
    record_parameter_file,
    save_review,
)
from dockstart_core.autogrid import (  # noqa: E402
    AD4ZN_NBP_R_EPS,
    generate_maps,
    set_scoring_protocol,
    validate_active_maps,
)
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    _validate_ad4_maps_post_run_integrity,
    _validate_execute_prerequisites,
    create_project,
    generate_vina_config,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    prepare_vina_run,
    update_box_params,
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


RECEPTOR_PDBQT = "".join(
    (
        "REMARK three-coordinate zinc integration fixture\n",
        _atom_line(
            1,
            "ZN",
            "ZN",
            "A",
            500,
            0.0,
            0.0,
            0.0,
            1.25,
            "Zn",
            record_type="HETATM",
        ),
        _atom_line(2, "NE2", "HIS", "A", 10, 2.0, 0.0, 0.0, -0.2, "NA"),
        _atom_line(3, "OD1", "ASN", "A", 11, 0.0, 2.0, 0.0, -0.4, "OA"),
        _atom_line(4, "SG", "CYS", "A", 12, 0.0, 0.0, 2.0, -0.2, "SA"),
    )
)
LIGAND_PDBQT = (
    "ROOT\n"
    "ATOM      1  C   LIG     1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
    "ATOM      2  N   LIG     1       1.200   0.000   0.000  1.00  0.00    -0.100 NA\n"
    "ENDROOT\n"
    "TORSDOF 0\n"
)
PARAMETER_TEXT = "\n".join(
    (
        "# test fixture; not a distributed AD4Zn parameter asset",
        "# GNU General Public License, version 2 or later",
        "FE_coeff_vdW 0.1662",
        "FE_coeff_hbond 0.1209",
        "FE_coeff_estat 0.1406",
        "FE_coeff_desolv 0.1322",
        "FE_coeff_tors 0.2983",
        "atom_par C 4.00 0.150 33.5103 -0.00143 0.0 0.0 0 -1 -1 0",
        "atom_par NA 3.50 0.160 22.4493 -0.00162 1.9 5.0 4 -1 -1 1",
        "atom_par OA 3.20 0.200 17.1573 -0.00251 1.9 5.0 5 -1 -1 2",
        "atom_par SA 4.00 0.200 33.5103 -0.00214 2.5 1.0 5 -1 -1 6",
        "atom_par ZN 1.48 0.550 1.7000 -0.00110 0.0 0.0 0 -1 -1 4",
        "atom_par TZ 1.00 0.000 0.0000 0.00000 0.0 0.0 0 -1 -1 0",
        "",
    )
)


class AD4ZnProjectIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        reference_sha256 = hashlib.sha256(
            PARAMETER_TEXT.encode("utf-8")
        ).hexdigest()
        patcher = patch.object(
            ad4zn_module,
            "SUPPORTED_PARAMETER_REFERENCE_SHA256",
            reference_sha256,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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
        for name in (
            "receptor.maps.fld",
            *(f"receptor.{atom_type}.map" for atom_type in ligand_types),
            "receptor.e.map",
            "receptor.d.map",
        ):
            (root / name).write_text(f"mock {name}\n", encoding="utf-8")
        (root / log_file).write_text(
            "AutoGrid 4.2.7\nSuccessful Completion\n",
            encoding="utf-8",
        )
        return {
            "ok": True,
            "command": ["autogrid4", "-p", gpf_file, "-l", log_file],
            "exit_code": 0,
            "stdout": "AutoGrid complete\n",
            "stderr": "",
            "error": "",
        }

    @staticmethod
    def _tool(
        key: str,
        name: str,
        version: str,
        path: Path,
    ) -> ToolCheckResult:
        return ToolCheckResult(
            key=key,
            name=name,
            status="ok",
            version=version,
            path=str(path),
            message=f"已检测到 {name}。",
            source="configured",
        )

    def _prepared_project(self, root: str) -> tuple[Path, Path]:
        created = create_project("ad4zn_integration", root)
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])
        receptor = Path(root) / "receptor.pdbqt"
        ligand = Path(root) / "ligand.pdbqt"
        parameter = Path(root) / "user_AD4Zn.dat"
        receptor.write_text(RECEPTOR_PDBQT, encoding="utf-8")
        ligand.write_text(LIGAND_PDBQT, encoding="utf-8")
        parameter.write_text(PARAMETER_TEXT, encoding="utf-8")
        self.assertTrue(
            import_receptor_pdbqt(str(project_dir), str(receptor))["ok"]
        )
        self.assertTrue(
            import_ligand_pdbqt(str(project_dir), str(ligand))["ok"]
        )
        self.assertTrue(
            update_box_params(
                str(project_dir),
                {
                    "center_x": 0,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 20,
                    "size_y": 20,
                    "size_z": 20,
                },
            )["ok"]
        )
        self.assertTrue(
            set_scoring_protocol(str(project_dir), "ad4zn_beta")["ok"]
        )
        status = get_ad4zn_status(str(project_dir))
        review = save_review(
            str(project_dir),
            {
                "selected_site_id": status["sites"][0]["site_id"],
                "confirmations": {
                    key: True for key in REQUIRED_CONFIRMATIONS
                },
            },
        )
        self.assertTrue(review["ok"], review)
        prepared = prepare_ad4zn_receptor(str(project_dir), {})
        self.assertTrue(prepared["ok"], prepared)
        recorded = record_parameter_file(str(project_dir), parameter)
        self.assertTrue(recorded["preparation_ready"], recorded)
        return project_dir, parameter

    def test_ad4zn_maps_run_snapshots_and_integrity_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter = self._prepared_project(root)
            autogrid = Path(root) / "autogrid4.exe"
            autogrid.write_bytes(b"mock AutoGrid 4.2.7")
            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=self._tool(
                    "autogrid4",
                    "AutoGrid4",
                    "4.2.7.x.2019-07-11",
                    autogrid,
                ),
            ):
                generated = generate_maps(
                    str(project_dir),
                    runner=self._fake_autogrid,
                )
                self.assertTrue(generated["ok"], generated)
                maps_status = validate_active_maps(str(project_dir))
            self.assertTrue(maps_status["ready"], maps_status)
            manifest = generated["manifest"]
            self.assertEqual(manifest["protocol_id"], "ad4zn_beta")
            self.assertEqual(manifest["stability"], "beta")
            coverage = manifest["ad4zn"]["box_coverage"]
            self.assertEqual(
                coverage["method"],
                "ad4zn_zn_tz_requested_box_and_autogrid_npts_spacing_v1",
            )
            self.assertEqual(coverage["interval_semantics"], "closed")
            self.assertTrue(
                coverage["all_zn_tz_inside_requested_box"]
            )
            self.assertTrue(
                coverage["all_zn_tz_inside_effective_grid"]
            )
            self.assertEqual(set(coverage["markers"]), {"ZN", "TZ"})
            self.assertEqual(
                manifest["grid"]["requested_box"],
                {
                    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "size": {"x": 20.0, "y": 20.0, "z": 20.0},
                },
            )
            project_payload = json.loads(
                (project_dir / "project.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                project_payload["ad4zn"]["box_coverage_sha256"],
                manifest["ad4zn"]["box_coverage_sha256"],
            )
            self.assertEqual(
                manifest["ad4zn"]["algorithm"]["algorithm_version"],
                "1.2",
            )
            self.assertEqual(
                manifest["ad4zn"]["algorithm"][
                    "algorithm_reference_sha256"
                ],
                "ccfa97e10614b30d32839935d3fc72a3038d43206caf5c1e7f9e0da96693e88d",
            )
            self.assertFalse(
                manifest["ad4zn"]["scientific_scope"][
                    "multinuclear_metal_supported"
                ]
            )
            self.assertEqual(
                manifest["autogrid"]["log_summary"]["successful_completion"],
                True,
            )
            receptor_snapshot_relative = Path(
                "maps",
                generated["map_set_id"],
                "inputs",
                "receptor_tz.pdbqt",
            ).as_posix()
            self.assertEqual(
                manifest["receptor"]["relative_path"],
                receptor_snapshot_relative,
            )
            receptor_snapshot = project_dir / receptor_snapshot_relative
            self.assertTrue(receptor_snapshot.is_file())
            self.assertEqual(
                manifest["receptor"]["size_bytes"],
                receptor_snapshot.stat().st_size,
            )
            self.assertEqual(
                manifest["receptor"]["sha256"],
                hashlib.sha256(receptor_snapshot.read_bytes()).hexdigest(),
            )
            gpf = (
                project_dir
                / "maps"
                / generated["map_set_id"]
                / "receptor.gpf"
            ).read_text(encoding="utf-8")
            self.assertIn("dielectric -0.1465", gpf)
            for line in AD4ZN_NBP_R_EPS:
                self.assertIn(line, gpf)
            self.assertIn("parameter_file inputs/AD4Zn.dat", gpf)
            self.assertIn("receptor inputs/receptor_tz.pdbqt", gpf)

            configured = generate_vina_config(str(project_dir))
            self.assertTrue(configured["ok"], configured)
            vina = Path(root) / "vina.exe"
            vina.write_bytes(b"mock Vina 1.2.7")
            with patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._tool(
                    "vina",
                    "AutoDock Vina",
                    "1.2.7",
                    vina,
                ),
            ):
                run = prepare_vina_run(str(project_dir))
            self.assertTrue(run["ok"], run)
            metadata = run["metadata"]
            self.assertEqual(
                metadata["docking_protocol"]["protocol_id"],
                "ad4zn_beta",
            )
            self.assertEqual(metadata["scoring_function"], "ad4")
            self.assertIn("--maps", metadata["command"])
            self.assertNotIn("--receptor", metadata["command"])
            self.assertEqual(
                metadata["command"][metadata["command"].index("--scoring") + 1],
                "ad4",
            )
            self.assertEqual(
                set(metadata["snapshots"]["ad4zn"]),
                {
                    "original_receptor",
                    "parameter_file",
                    "gpf",
                    "protocol_record",
                },
            )
            before = _validate_execute_prerequisites(
                str(project_dir),
                run["run_id"],
                metadata,
            )
            self.assertTrue(before["ok"], before)
            post = _validate_ad4_maps_post_run_integrity(
                str(project_dir),
                run["run_id"],
                metadata,
            )
            self.assertTrue(post["ok"], post)

            parameter_snapshot = (
                project_dir
                / metadata["snapshots"]["ad4zn"]["parameter_file"][
                    "relative_path"
                ]
            )
            parameter_snapshot.write_text(
                PARAMETER_TEXT + "# changed during run\n",
                encoding="utf-8",
            )
            rejected = _validate_ad4_maps_post_run_integrity(
                str(project_dir),
                run["run_id"],
                metadata,
            )
            self.assertFalse(rejected["ok"])
            self.assertEqual(
                rejected["error"]["code"],
                "RUN_AD4ZN_POST_HASH_MISMATCH",
            )

    def test_zn_tz_outside_requested_box_blocks_before_autogrid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter = self._prepared_project(root)
            moved = update_box_params(
                str(project_dir),
                {
                    "center_x": 100,
                    "center_y": 100,
                    "center_z": 100,
                    "size_x": 20,
                    "size_y": 20,
                    "size_z": 20,
                },
            )
            self.assertTrue(moved["ok"], moved)
            autogrid = Path(root) / "autogrid4.exe"
            autogrid.write_bytes(b"mock AutoGrid 4.2.7")
            runner = Mock(side_effect=self._fake_autogrid)

            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=self._tool(
                    "autogrid4",
                    "AutoGrid4",
                    "4.2.7",
                    autogrid,
                ),
            ):
                generated = generate_maps(
                    str(project_dir),
                    runner=runner,
                )

            self.assertFalse(generated["ok"], generated)
            self.assertEqual(
                generated["error"]["code"],
                "AD4ZN_ZN_TZ_OUTSIDE_REQUESTED_BOX",
            )
            runner.assert_not_called()

    def test_zn_tz_outside_effective_grid_blocks_before_autogrid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter = self._prepared_project(root)
            autogrid = Path(root) / "autogrid4.exe"
            autogrid.write_bytes(b"mock AutoGrid 4.2.7")
            runner = Mock(side_effect=self._fake_autogrid)

            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=self._tool(
                    "autogrid4",
                    "AutoGrid4",
                    "4.2.7",
                    autogrid,
                ),
            ):
                generated = generate_maps(
                    str(project_dir),
                    {
                        "spacing": 0.1,
                        "grid_points": [2, 2, 2],
                    },
                    runner=runner,
                )

            self.assertFalse(generated["ok"], generated)
            self.assertEqual(
                generated["error"]["code"],
                "AD4ZN_ZN_TZ_OUTSIDE_EFFECTIVE_GRID",
            )
            runner.assert_not_called()

    def test_zn_tz_on_closed_box_and_grid_boundaries_is_allowed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter = self._prepared_project(root)
            moved = update_box_params(
                str(project_dir),
                {
                    "center_x": -10,
                    "center_y": -10,
                    "center_z": -10,
                    "size_x": 20,
                    "size_y": 20,
                    "size_z": 20,
                },
            )
            self.assertTrue(moved["ok"], moved)
            autogrid = Path(root) / "autogrid4.exe"
            autogrid.write_bytes(b"mock AutoGrid 4.2.7")
            runner = Mock(side_effect=self._fake_autogrid)

            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=self._tool(
                    "autogrid4",
                    "AutoGrid4",
                    "4.2.7",
                    autogrid,
                ),
            ):
                generated = generate_maps(
                    str(project_dir),
                    {
                        "spacing": 1.0,
                        "grid_points": [20, 20, 20],
                    },
                    runner=runner,
                )
                validated = (
                    validate_active_maps(str(project_dir))
                    if generated.get("ok")
                    else generated
                )

            self.assertTrue(generated["ok"], generated)
            runner.assert_called_once()
            coverage = generated["manifest"]["ad4zn"]["box_coverage"]
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
            self.assertTrue(validated["ready"], validated)

    def test_active_maps_recompute_and_reject_coverage_tampering(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter = self._prepared_project(root)
            autogrid = Path(root) / "autogrid4.exe"
            autogrid.write_bytes(b"mock AutoGrid 4.2.7")
            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=self._tool(
                    "autogrid4",
                    "AutoGrid4",
                    "4.2.7",
                    autogrid,
                ),
            ):
                generated = generate_maps(
                    str(project_dir),
                    runner=self._fake_autogrid,
                )
            self.assertTrue(generated["ok"], generated)
            manifest_path = project_dir / generated["manifest_file"]
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes.decode("utf-8"))

            without_coverage = json.loads(json.dumps(manifest))
            without_coverage["ad4zn"].pop("box_coverage")
            manifest_path.write_text(
                json.dumps(
                    without_coverage,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            missing = validate_active_maps(str(project_dir))
            self.assertFalse(missing["ready"], missing)
            self.assertIn(
                "缺少 ZN/TZ Box 覆盖记录",
                "；".join(missing["issues"]),
            )

            tampered_coverage = json.loads(json.dumps(manifest))
            tampered_coverage["ad4zn"]["box_coverage"]["markers"]["ZN"][
                "coordinate_angstrom"
            ]["x"] = 0.5
            manifest_path.write_text(
                json.dumps(
                    tampered_coverage,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            tampered = validate_active_maps(str(project_dir))
            self.assertFalse(tampered["ready"], tampered)
            self.assertIn(
                "覆盖记录摘要不匹配",
                "；".join(tampered["issues"]),
            )

            manifest_path.write_bytes(manifest_bytes)
            prepared_path = (
                project_dir
                / manifest["receptor"]["source_relative_path"]
            )
            prepared_bytes = prepared_path.read_bytes()
            prepared_lines = prepared_bytes.decode("utf-8").splitlines(
                keepends=True
            )
            for index, line in enumerate(prepared_lines):
                if line.split() and line.split()[-1].upper() == "TZ":
                    prepared_lines[index] = (
                        line[:30] + f"{0.500:8.3f}" + line[38:]
                    )
                    break
            prepared_path.write_text(
                "".join(prepared_lines),
                encoding="utf-8",
            )
            coordinate_changed = validate_active_maps(str(project_dir))
            self.assertFalse(
                coordinate_changed["ready"],
                coordinate_changed,
            )
            self.assertIn(
                "重算的 ZN/TZ Box 覆盖与 manifest 不一致",
                "；".join(coordinate_changed["issues"]),
            )
            prepared_path.write_bytes(prepared_bytes)

            current_box = generated["manifest"]["grid"]["actual_size"]
            moved = update_box_params(
                str(project_dir),
                {
                    "center_x": 1,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": current_box["x"],
                    "size_y": current_box["y"],
                    "size_z": current_box["z"],
                },
            )
            self.assertTrue(moved["ok"], moved)
            box_changed = validate_active_maps(str(project_dir))
            self.assertFalse(box_changed["ready"], box_changed)
            self.assertIn(
                "当前 Box 的 center_x 与 maps 网格不一致",
                "；".join(box_changed["issues"]),
            )

    def test_autogrid_426_is_a_hard_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter = self._prepared_project(root)
            autogrid = Path(root) / "autogrid4.exe"
            autogrid.write_bytes(b"mock AutoGrid 4.2.6")
            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=self._tool(
                    "autogrid4",
                    "AutoGrid4",
                    "4.2.6",
                    autogrid,
                ),
            ):
                generated = generate_maps(
                    str(project_dir),
                    runner=self._fake_autogrid,
                )
            self.assertFalse(generated["ok"])
            self.assertEqual(
                generated["error"]["code"],
                "AD4ZN_AUTOGRID_VERSION_UNSUPPORTED",
            )


if __name__ == "__main__":
    unittest.main()
