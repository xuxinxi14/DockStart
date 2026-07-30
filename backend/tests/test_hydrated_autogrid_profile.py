from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.autogrid import generate_hydrated_base_maps  # noqa: E402
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    create_project,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    update_box_params,
)


RECEPTOR_PDBQT = (
    "ATOM      1  C   REC A   1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
    "ATOM      2  O   REC A   1       1.200   0.000   0.000  1.00  0.00    -0.200 OA\n"
)
STANDARD_LIGAND_PDBQT = (
    "ROOT\n"
    "ATOM      1  C   LIG     1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
    "ENDROOT\n"
    "TORSDOF 0\n"
)
HYDRATED_LIGAND_PDBQT = (
    "ROOT\n"
    "ATOM      1  C   LIG     1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
    "ATOM      2  N   LIG     1       1.200   0.000   0.000  1.00  0.00    -0.100 NA\n"
    "ATOM      3 WAT  LIG     1       2.500   0.000   0.000  1.00  0.00     0.000 W\n"
    "ENDROOT\n"
    "TORSDOF 0\n"
)


class HydratedAutoGridProfileTests(unittest.TestCase):
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
        (root / log_file).write_text("Successful Completion\n", encoding="utf-8")
        return {
            "ok": True,
            "command": ["autogrid4", "-p", gpf_file, "-l", log_file],
            "exit_code": 0,
            "stdout": "complete\n",
            "stderr": "",
            "error": "",
        }

    def _project(self, root: str) -> Path:
        created = create_project("hydrated_maps", root)
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])
        receptor = Path(root) / "receptor.pdbqt"
        ligand = Path(root) / "ligand.pdbqt"
        receptor.write_text(RECEPTOR_PDBQT, encoding="utf-8")
        ligand.write_text(STANDARD_LIGAND_PDBQT, encoding="utf-8")
        self.assertTrue(import_receptor_pdbqt(str(project_dir), str(receptor))["ok"])
        self.assertTrue(import_ligand_pdbqt(str(project_dir), str(ligand))["ok"])
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
        return project_dir

    @staticmethod
    def _hydrated_file(project_dir: Path) -> Path:
        relative = Path(
            "protocols",
            "hydrated",
            "ligand_preparations",
            "hydrated_ligand_001",
            "hydrated_ligand.pdbqt",
        )
        target = project_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(HYDRATED_LIGAND_PDBQT, encoding="utf-8")
        return relative

    def test_hydrated_profile_omits_w_adds_oa_hd_and_does_not_activate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            hydrated_relative = Path(
                "protocols",
                "hydrated",
                "ligand_preparations",
                "hydrated_ligand_001",
                "hydrated_ligand.pdbqt",
            )
            hydrated = project_dir / hydrated_relative
            hydrated.parent.mkdir(parents=True)
            hydrated.write_text(HYDRATED_LIGAND_PDBQT, encoding="utf-8")
            executable = Path(temp_dir) / "autogrid4.exe"
            executable.write_bytes(b"mock autogrid")
            detection = ToolCheckResult(
                key="autogrid4",
                name="AutoGrid4",
                status="ok",
                version="4.2.6",
                path=str(executable),
                message="ok",
                source="configured",
            )

            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=detection,
            ):
                project_before = json.loads(
                    (project_dir / "project.json").read_text(encoding="utf-8")
                )
                result = generate_hydrated_base_maps(
                    str(project_dir),
                    hydrated_relative.as_posix(),
                    runner=self._fake_autogrid,
                )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["map_set_id"], "hydrated_001")
            manifest = result["manifest"]
            self.assertEqual(
                manifest["protocol_id"],
                "hydrated_ad4_experimental",
            )
            self.assertEqual(manifest["stability"], "experimental")
            self.assertEqual(manifest["autogrid"]["version"], "4.2.6")
            self.assertEqual(manifest["ligand"]["atom_types"], ["C", "NA", "W"])
            self.assertEqual(
                manifest["maps"]["ligand_atom_types"],
                ["C", "HD", "NA", "OA"],
            )
            gpf = project_dir / "maps" / result["map_set_id"] / "receptor.gpf"
            gpf_text = gpf.read_text(encoding="utf-8")
            self.assertIn("ligand_types C HD NA OA", gpf_text)
            self.assertNotIn("ligand_types C HD NA OA W", gpf_text)
            project_json = json.loads(
                (project_dir / "project.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("ad4_maps", project_json)
            self.assertEqual(
                project_json.get("docking_protocol"),
                project_before.get("docking_protocol"),
            )

    def test_hydrated_generation_rejects_old_or_unknown_autogrid_version(
        self,
    ) -> None:
        for version in ("4.2.5", "unknown"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp_dir:
                project_dir = self._project(temp_dir)
                hydrated_relative = Path(
                    "protocols",
                    "hydrated",
                    "ligand_preparations",
                    "hydrated_ligand_001",
                    "hydrated_ligand.pdbqt",
                )
                hydrated = project_dir / hydrated_relative
                hydrated.parent.mkdir(parents=True)
                hydrated.write_text(HYDRATED_LIGAND_PDBQT, encoding="utf-8")
                executable = Path(temp_dir) / "autogrid4.exe"
                executable.write_bytes(b"mock autogrid")
                detection = ToolCheckResult(
                    key="autogrid4",
                    name="AutoGrid4",
                    status="ok",
                    version=version,
                    path=str(executable),
                    message="ok",
                    source="configured",
                )
                runner = Mock(side_effect=self._fake_autogrid)

                with patch(
                    "dockstart_core.autogrid.autogrid_adapter.detect",
                    return_value=detection,
                ):
                    result = generate_hydrated_base_maps(
                        str(project_dir),
                        hydrated_relative.as_posix(),
                        runner=runner,
                    )

                self.assertFalse(result["ok"], result)
                self.assertEqual(
                    result["error"]["code"],
                    "HYDRATED_AUTOGRID_VERSION_UNSUPPORTED",
                )
                self.assertIn("required>=4.2.6", result["error"]["raw_error"])
                runner.assert_not_called()

    def test_hydrated_profile_requires_w_and_rejects_custom_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            relative = Path("protocols", "hydrated", "plain.pdbqt")
            plain = project_dir / relative
            plain.parent.mkdir(parents=True)
            plain.write_text(STANDARD_LIGAND_PDBQT, encoding="utf-8")

            result = generate_hydrated_base_maps(
                str(project_dir),
                relative.as_posix(),
                runner=self._fake_autogrid,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(
                result["error"]["code"],
                "HYDRATED_LIGAND_W_TYPE_MISSING",
            )

            option_error = generate_hydrated_base_maps(
                str(project_dir),
                relative.as_posix(),
                {"ligand_atom_types": ["C"]},
                runner=self._fake_autogrid,
            )
            self.assertFalse(option_error["ok"])
            self.assertEqual(
                option_error["error"]["code"],
                "HYDRATED_MAPS_OPTIONS_UNSUPPORTED",
            )

    def test_hydrated_grid_undercoverage_fails_before_autogrid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            hydrated_relative = self._hydrated_file(project_dir)
            self.assertTrue(
                update_box_params(
                    str(project_dir),
                    {
                        "center_x": 0,
                        "center_y": 0,
                        "center_z": 0,
                        "size_x": 15,
                        "size_y": 15,
                        "size_z": 15,
                    },
                )["ok"]
            )
            runner = Mock(side_effect=self._fake_autogrid)

            under = generate_hydrated_base_maps(
                str(project_dir),
                hydrated_relative.as_posix(),
                {
                    "spacing": 0.375,
                    "grid_points": {"x": 38, "y": 38, "z": 38},
                },
                runner=runner,
            )

            self.assertFalse(under["ok"], under)
            self.assertEqual(
                under["error"]["code"],
                "HYDRATED_GRID_UNDER_COVERS_REQUESTED_BOX",
            )
            runner.assert_not_called()

            executable = Path(temp_dir) / "autogrid4.exe"
            executable.write_bytes(b"mock autogrid")
            detection = ToolCheckResult(
                key="autogrid4",
                name="AutoGrid4",
                status="ok",
                version="4.2.6",
                path=str(executable),
                message="ok",
                source="configured",
            )
            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=detection,
            ):
                covered = generate_hydrated_base_maps(
                    str(project_dir),
                    hydrated_relative.as_posix(),
                    {
                        "spacing": 0.375,
                        "grid_points": {
                            "x": 40,
                            "y": 40,
                            "z": 40,
                        },
                    },
                    runner=runner,
                )
            self.assertTrue(covered["ok"], covered)
            self.assertTrue(
                covered["manifest"]["grid_coverage"][
                    "covers_requested_box"
                ]
            )
            self.assertEqual(runner.call_count, 1)

            self.assertTrue(
                update_box_params(
                    str(project_dir),
                    {
                        "center_x": 0,
                        "center_y": 0,
                        "center_z": 0,
                        "size_x": 8,
                        "size_y": 8,
                        "size_z": 8,
                    },
                )["ok"]
            )
            runner.reset_mock()
            old_small_grid = generate_hydrated_base_maps(
                str(project_dir),
                hydrated_relative.as_posix(),
                {
                    "spacing": 1.0,
                    "grid_points": {"x": 2, "y": 2, "z": 2},
                },
                runner=runner,
            )
            self.assertFalse(old_small_grid["ok"], old_small_grid)
            self.assertEqual(
                old_small_grid["error"]["code"],
                "HYDRATED_GRID_UNDER_COVERS_REQUESTED_BOX",
            )
            runner.assert_not_called()

    def test_hydrated_default_grid_does_not_silently_cap_at_126(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            hydrated_relative = self._hydrated_file(project_dir)
            self.assertTrue(
                update_box_params(
                    str(project_dir),
                    {
                        "center_x": 0,
                        "center_y": 0,
                        "center_z": 0,
                        "size_x": 47.25,
                        "size_y": 47.25,
                        "size_z": 47.25,
                    },
                )["ok"]
            )
            executable = Path(temp_dir) / "autogrid4.exe"
            executable.write_bytes(b"mock autogrid")
            detection = ToolCheckResult(
                key="autogrid4",
                name="AutoGrid4",
                status="ok",
                version="4.2.6",
                path=str(executable),
                message="ok",
                source="configured",
            )
            runner = Mock(side_effect=self._fake_autogrid)
            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=detection,
            ):
                boundary = generate_hydrated_base_maps(
                    str(project_dir),
                    hydrated_relative.as_posix(),
                    runner=runner,
                )
            self.assertTrue(boundary["ok"], boundary)
            self.assertEqual(
                boundary["manifest"]["grid"]["grid_points"],
                {"x": 126, "y": 126, "z": 126},
            )
            self.assertEqual(runner.call_count, 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            hydrated_relative = self._hydrated_file(project_dir)
            self.assertTrue(
                update_box_params(
                    str(project_dir),
                    {
                        "center_x": 0,
                        "center_y": 0,
                        "center_z": 0,
                        "size_x": 47.250001,
                        "size_y": 47.25,
                        "size_z": 47.25,
                    },
                )["ok"]
            )
            runner = Mock(side_effect=self._fake_autogrid)

            too_large = generate_hydrated_base_maps(
                str(project_dir),
                hydrated_relative.as_posix(),
                runner=runner,
            )

            self.assertFalse(too_large["ok"], too_large)
            self.assertEqual(
                too_large["error"]["code"],
                "MAPS_GRID_TOO_LARGE",
            )
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
