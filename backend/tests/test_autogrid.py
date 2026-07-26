from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.autogrid import (  # noqa: E402
    generate_maps,
    get_maps_defaults,
    get_maps_status,
    validate_active_maps,
)
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    create_project,
    execute_prepared_vina_run,
    generate_vina_config,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    prepare_vina_run,
    update_box_params,
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

    def _autogrid_ok(self, executable: Path) -> ToolCheckResult:
        return ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="ok",
            version="4.2.6",
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
