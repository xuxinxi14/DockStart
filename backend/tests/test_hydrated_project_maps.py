from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import hydrated  # noqa: E402
from dockstart_core.hydrated_maps import parse_autogrid_map  # noqa: E402
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
    "ATOM      1  C   LIG A   1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
    "ENDROOT\n"
    "TORSDOF 0\n"
)


def _atom_line(
    serial: int,
    name: str,
    x: float,
    y: float,
    z: float,
    atom_type: str,
) -> str:
    return (
        f"ATOM  {serial:5d} {name:^4} LIG A   1    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}"
        f"{1.00:6.2f}{0.00:6.2f}    "
        f"{0.000:6.3f} {atom_type:>2}\n"
    )


def _hydrated_pdbqt() -> str:
    return "".join(
        [
            "ROOT\n",
            _atom_line(1, "C1", 0.0, 0.0, 0.0, "C"),
            _atom_line(2, "O1", 1.2, 0.0, 0.0, "OA"),
            _atom_line(3, "WAT", 2.0, 1.0, 0.5, "W"),
            _atom_line(4, "WAT", 3.0, 1.0, 0.5, "W"),
            "ENDROOT\n",
            "TORSDOF 0\n",
        ]
    )


def _sdf() -> str:
    return """DockStart hydrated
  DockStart          3D

  2  1  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2000    0.0000    0.4000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0  0  0  0
M  END
$$$$
"""


class _PreparationRunner:
    def __init__(self, rdkit_module: Path, meeko_module: Path) -> None:
        self.rdkit_module = rdkit_module
        self.meeko_module = meeko_module

    def __call__(
        self,
        command: list[str],
        cwd: str | Path,
        timeout: int = 300,
    ) -> SimpleNamespace:
        _ = cwd, timeout
        normalized = [str(item) for item in command]
        mode = normalized[4]
        if mode == "probe":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "rdkit_version": "2026.3.3-test",
                        "rdkit_module_file": str(self.rdkit_module),
                        "meeko_version": "0.7.1-test",
                        "meeko_module_file": str(self.meeko_module),
                        "writer_interface": "PDBQTWriterLegacy",
                        "hydrate_supported": True,
                        "probe_water_count": 2,
                    }
                )
                + "\n",
                stderr="",
            )
        if mode != "prepare":
            raise AssertionError(normalized)
        Path(normalized[6]).write_text(_sdf(), encoding="utf-8")
        Path(normalized[7]).write_text(_hydrated_pdbqt(), encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "rdkit_version": "2026.3.3-test",
                    "meeko_version": "0.7.1-test",
                    "writer_interface": "PDBQTWriterLegacy",
                    "atom_count": 8,
                    "bond_count": 7,
                    "water_count": 2,
                }
            )
            + "\n",
            stderr="",
        )


def _map_text(
    values: list[float],
    *,
    spacing: float,
    nelements: tuple[int, int, int],
    center: tuple[float, float, float],
) -> str:
    return "\n".join(
        [
            "GRID_PARAMETER_FILE receptor.gpf",
            "GRID_DATA_FILE receptor.maps.fld",
            "MACROMOLECULE inputs/receptor.pdbqt",
            f"SPACING {spacing}",
            f"NELEMENTS {nelements[0]} {nelements[1]} {nelements[2]}",
            f"CENTER {center[0]} {center[1]} {center[2]}",
            *(str(value) for value in values),
        ]
    ) + "\n"


class HydratedProjectMapsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.counter = 0
        self.tool_root = self.base / "tools"
        self.tool_root.mkdir()
        self.python_file = self.tool_root / "python.exe"
        self.rdkit_module = self.tool_root / "rdkit_init.py"
        self.meeko_module = self.tool_root / "meeko_init.py"
        self.autogrid_file = self.tool_root / "autogrid4.exe"
        self.python_file.write_bytes(b"python-v1")
        self.rdkit_module.write_bytes(b"rdkit-v1")
        self.meeko_module.write_bytes(b"meeko-v1")
        self.autogrid_file.write_bytes(b"autogrid-v1")

    def _tools(self) -> dict:
        return {
            "python": {
                "status": "ok",
                "version": "Python 3.11.15",
                "path": str(self.python_file),
                "source": "bundled",
            },
            "rdkit": {
                "status": "ok",
                "version": "2026.3.3-test",
                "capabilities": {"import": {"status": "ok"}},
            },
            "meeko": {
                "status": "ok",
                "version": "0.7.1-test",
                "capabilities": {
                    "import": {"status": "ok"},
                    "ligand_preparation": {"status": "ok"},
                },
            },
        }

    def _autogrid_detection(self) -> ToolCheckResult:
        return ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="ok",
            version="4.2.7",
            path=str(self.autogrid_file),
            message="已检测到 AutoGrid4。",
            source="configured",
        )

    def _create_ready_project(self) -> Path:
        self.counter += 1
        name = f"hydrated_maps_{self.counter}"
        created = create_project(name, str(self.base))
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])

        receptor_source = self.base / f"{name}_receptor.pdbqt"
        ligand_source = self.base / f"{name}_ligand.pdbqt"
        receptor_source.write_text(RECEPTOR_PDBQT, encoding="utf-8")
        ligand_source.write_text(STANDARD_LIGAND_PDBQT, encoding="utf-8")
        self.assertTrue(
            import_receptor_pdbqt(str(project_dir), str(receptor_source))["ok"]
        )
        self.assertTrue(
            import_ligand_pdbqt(str(project_dir), str(ligand_source))["ok"]
        )
        self.assertTrue(
            update_box_params(
                str(project_dir),
                {
                    "center_x": 1,
                    "center_y": 2,
                    "center_z": 3,
                    "size_x": 8,
                    "size_y": 8,
                    "size_z": 8,
                },
            )["ok"]
        )
        raw = project_dir / "raw" / "ligand.sdf"
        raw.write_text(_sdf(), encoding="utf-8")
        project_json = project_dir / "project.json"
        payload = json.loads(project_json.read_text(encoding="utf-8"))
        payload["ligand"]["raw_file"] = "raw/ligand.sdf"
        payload["docking_protocol"] = {
            "engine": "vina",
            "protocol_id": "rigid_single",
            "receptor_mode": "rigid",
        }
        project_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        preparation_runner = _PreparationRunner(
            self.rdkit_module,
            self.meeko_module,
        )
        with (
            patch("dockstart_core.hydrated._tool_status", return_value=self._tools()),
            patch(
                "dockstart_core.hydrated.meeko_adapter.run_preparation_command",
                side_effect=preparation_runner,
            ),
        ):
            prepared = hydrated.prepare_hydrated_ligand(str(project_dir))
        self.assertTrue(prepared["ok"], prepared)
        return project_dir

    @staticmethod
    def _project_data(project_dir: Path) -> dict:
        return json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8")
        )

    def _runner(
        self,
        *,
        mutate: object | None = None,
        mismatch_hd_geometry: bool = False,
        omit_name: str = "",
    ):
        def run(
            executable: str,
            gpf_file: str,
            log_file: str,
            working_directory: str | Path,
            **_kwargs: object,
        ) -> dict[str, object]:
            root = Path(working_directory)
            gpf = (root / gpf_file).read_text(encoding="utf-8")
            ligand_types = next(
                line.split()[1:]
                for line in gpf.splitlines()
                if line.startswith("ligand_types ")
            )
            points = tuple(
                int(value)
                for value in next(
                    line.split()[1:]
                    for line in gpf.splitlines()
                    if line.startswith("npts ")
                )
            )
            spacing = float(
                next(
                    line.split()[1]
                    for line in gpf.splitlines()
                    if line.startswith("spacing ")
                )
            )
            center = tuple(
                float(value)
                for value in next(
                    line.split()[1:]
                    for line in gpf.splitlines()
                    if line.startswith("gridcenter ")
                )
            )
            names = [
                "receptor.maps.fld",
                *(f"receptor.{atom_type}.map" for atom_type in ligand_types),
                "receptor.e.map",
                "receptor.d.map",
            ]
            point_count = math.prod(value + 1 for value in points)
            for name in names:
                if name == omit_name:
                    continue
                path = root / name
                if name == "receptor.maps.fld":
                    path.write_text("validated fld\n", encoding="ascii")
                    continue
                value = (
                    -1.0
                    if name == "receptor.OA.map"
                    else -0.5
                    if name == "receptor.HD.map"
                    else -0.1
                )
                map_center = (
                    (center[0] + 1.0, center[1], center[2])
                    if mismatch_hd_geometry and name == "receptor.HD.map"
                    else center
                )
                path.write_text(
                    _map_text(
                        [value] * point_count,
                        spacing=spacing,
                        nelements=points,
                        center=map_center,
                    ),
                    encoding="ascii",
                )
            (root / log_file).write_text(
                "Successful Completion\n",
                encoding="utf-8",
            )
            if callable(mutate):
                mutate()
            return {
                "ok": True,
                "command": [
                    executable,
                    "-p",
                    gpf_file,
                    "-l",
                    log_file,
                ],
                "exit_code": 0,
                "stdout": "AutoGrid complete\n",
                "stderr": "",
                "error": "",
            }

        return run

    def _generate(
        self,
        project_dir: Path,
        *,
        runner: object | None = None,
    ) -> dict:
        with patch(
            "dockstart_core.hydrated.autogrid_adapter.detect",
            return_value=self._autogrid_detection(),
        ):
            return hydrated.generate_hydrated_maps(
                str(project_dir),
                {
                    "spacing": 1.0,
                    "grid_points": {"x": 2, "y": 2, "z": 2},
                },
                runner=runner or self._runner(),
            )

    def _status(self, project_dir: Path) -> dict:
        with patch(
            "dockstart_core.hydrated.autogrid_adapter.detect",
            return_value=self._autogrid_detection(),
        ):
            return hydrated.get_status(str(project_dir))

    def test_generates_independent_base_and_best_water_maps(self) -> None:
        project_dir = self._create_ready_project()
        before = self._project_data(project_dir)

        generated = self._generate(project_dir)

        self.assertTrue(generated["ok"], generated)
        self.assertTrue(generated["maps_ready"])
        self.assertEqual(generated["protocol_id"], "hydrated_ad4_experimental")
        manifest = generated["manifest"]
        self.assertEqual(manifest["status"], "ready")
        self.assertEqual(
            manifest["receptor"]["source_sha256"],
            manifest["receptor"]["sha256"],
        )
        self.assertEqual(
            manifest["ligand"]["source_sha256"],
            manifest["hydrated_ligand"]["sha256"],
        )
        self.assertIn("receptor.W.map", manifest["maps"]["required_files"])
        self.assertIn("W", manifest["maps"]["ligand_atom_types"])
        self.assertNotIn(
            "W",
            manifest["maps"]["autogrid_ligand_atom_types"],
        )
        self.assertIn(
            "OA",
            manifest["maps"]["autogrid_ligand_atom_types"],
        )
        self.assertIn(
            "HD",
            manifest["maps"]["autogrid_ligand_atom_types"],
        )
        self.assertTrue(
            all(
                not Path(str(item["relative_path"])).is_absolute()
                for item in manifest["maps"]["files"]
            )
        )
        water_path = project_dir / manifest["maps"]["water_map"]["relative_path"]
        parsed_water = parse_autogrid_map(water_path)
        self.assertAlmostEqual(parsed_water.values[0], -0.6)
        gpf = (
            project_dir
            / "maps"
            / generated["map_set_id"]
            / "receptor.gpf"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ligand_types W", gpf)
        self.assertNotIn("receptor.W.map", gpf)
        self.assertIn("OA", gpf)
        self.assertIn("HD", gpf)

        after = self._project_data(project_dir)
        self.assertEqual(after["ligand"]["file"], before["ligand"]["file"])
        self.assertEqual(after["box"], before["box"])
        self.assertEqual(after["docking_protocol"], before["docking_protocol"])
        state = after["hydrated_docking"]
        self.assertEqual(
            set(state),
            {"active_ligand_manifest", "active_maps_manifest", "updated_at"},
        )
        self.assertEqual(
            state["active_maps_manifest"],
            generated["active_maps_manifest"],
        )
        status = self._status(project_dir)
        self.assertTrue(status["preparation_ready"])
        self.assertEqual(status["maps_status"], "ready")
        self.assertTrue(status["maps_ready"])
        self.assertEqual(
            status["maps"]["prefix"],
            generated["maps_prefix"],
        )

    def test_rejects_unopened_options_and_flexible_receptor(self) -> None:
        project_dir = self._create_ready_project()
        unsupported = hydrated.generate_hydrated_maps(
            str(project_dir),
            {"ligand_atom_types": ["C"]},
        )
        self.assertFalse(unsupported["ok"])
        self.assertEqual(
            unsupported["error"]["code"],
            "HYDRATED_MAPS_OPTIONS_UNSUPPORTED",
        )
        self.assertFalse(
            list((project_dir / "maps").glob("hydrated_*"))
            if (project_dir / "maps").exists()
            else []
        )

        payload = self._project_data(project_dir)
        payload["docking_protocol"]["receptor_mode"] = "flexible"
        (project_dir / "project.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        flexible = hydrated.generate_hydrated_maps(str(project_dir))
        self.assertFalse(flexible["ok"])
        self.assertEqual(
            flexible["error"]["code"],
            "HYDRATED_MAPS_RIGID_RECEPTOR_REQUIRED",
        )

    def test_geometry_failure_keeps_audit_and_previous_pointer(self) -> None:
        project_dir = self._create_ready_project()
        first = self._generate(project_dir)
        self.assertTrue(first["ok"], first)
        old_pointer = self._project_data(project_dir)["hydrated_docking"][
            "active_maps_manifest"
        ]

        failed = self._generate(
            project_dir,
            runner=self._runner(mismatch_hd_geometry=True),
        )

        self.assertFalse(failed["ok"])
        self.assertEqual(
            self._project_data(project_dir)["hydrated_docking"][
                "active_maps_manifest"
            ],
            old_pointer,
        )
        failure_manifest = project_dir / failed["manifest_file"]
        self.assertTrue(failure_manifest.is_file())
        self.assertEqual(
            json.loads(failure_manifest.read_text(encoding="utf-8"))["status"],
            "failed",
        )

    def test_source_and_tool_toctou_block_activation(self) -> None:
        cases = ("receptor", "box", "hydrated_ligand", "autogrid")
        for case in cases:
            with self.subTest(case=case):
                project_dir = self._create_ready_project()

                def mutate() -> None:
                    if case == "receptor":
                        (project_dir / "prepared" / "receptor.pdbqt").write_text(
                            RECEPTOR_PDBQT + "REMARK changed\n",
                            encoding="utf-8",
                        )
                    elif case == "box":
                        payload = self._project_data(project_dir)
                        payload["box"]["center_x"] = 9.0
                        (project_dir / "project.json").write_text(
                            json.dumps(payload, ensure_ascii=False, indent=2)
                            + "\n",
                            encoding="utf-8",
                        )
                    elif case == "hydrated_ligand":
                        status = hydrated.get_status(str(project_dir))
                        ligand_path = (
                            project_dir
                            / status["manifest"]["outputs"]["hydrated_pdbqt"]["path"]
                        )
                        ligand_path.write_text(
                            _hydrated_pdbqt() + "REMARK changed\n",
                            encoding="utf-8",
                        )
                    else:
                        self.autogrid_file.write_bytes(
                            self.autogrid_file.read_bytes() + b"-changed"
                        )

                failed = self._generate(
                    project_dir,
                    runner=self._runner(mutate=mutate),
                )
                self.assertFalse(failed["ok"], failed)
                self.assertNotIn(
                    "active_maps_manifest",
                    self._project_data(project_dir)["hydrated_docking"],
                )
                self.assertEqual(
                    json.loads(
                        (project_dir / failed["manifest_file"]).read_text(
                            encoding="utf-8"
                        )
                    )["status"],
                    "failed",
                )
                if case == "autogrid":
                    self.autogrid_file.write_bytes(b"autogrid-v1")

    def test_status_invalidates_bound_sources_maps_and_manifests(self) -> None:
        cases = (
            "receptor",
            "box",
            "hydrated_ligand",
            "map",
            "base_manifest",
            "active_manifest",
        )
        for case in cases:
            with self.subTest(case=case):
                project_dir = self._create_ready_project()
                generated = self._generate(project_dir)
                self.assertTrue(generated["ok"], generated)
                manifest = generated["manifest"]
                if case == "receptor":
                    (project_dir / "prepared" / "receptor.pdbqt").write_text(
                        RECEPTOR_PDBQT + "REMARK changed\n",
                        encoding="utf-8",
                    )
                elif case == "box":
                    payload = self._project_data(project_dir)
                    payload["box"]["center_z"] = 8.0
                    (project_dir / "project.json").write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                elif case == "hydrated_ligand":
                    hydrated_path = (
                        project_dir
                        / manifest["hydrated_ligand"]["relative_path"]
                    )
                    hydrated_path.write_text(
                        _hydrated_pdbqt() + "REMARK changed\n",
                        encoding="utf-8",
                    )
                elif case == "map":
                    map_path = project_dir / manifest["maps"]["files"][0][
                        "relative_path"
                    ]
                    map_path.write_bytes(map_path.read_bytes() + b"changed\n")
                elif case == "base_manifest":
                    base_path = (
                        project_dir
                        / manifest["base_maps_manifest"]["relative_path"]
                    )
                    base_path.write_bytes(base_path.read_bytes() + b" ")
                else:
                    active_path = project_dir / generated["manifest_file"]
                    payload = json.loads(active_path.read_text(encoding="utf-8"))
                    payload["finished_at"] = "changed"
                    active_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

                status = self._status(project_dir)
                self.assertEqual(status["maps_status"], "invalid", status)
                self.assertFalse(status["maps_ready"])
                self.assertTrue(status["maps_issues"])

    def test_status_does_not_require_generation_tool_after_publication(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        generated = self._generate(project_dir)
        self.assertTrue(generated["ok"], generated)
        self.autogrid_file.write_bytes(b"autogrid-v2")

        status = self._status(project_dir)

        self.assertEqual(status["maps_status"], "ready", status)
        self.assertTrue(status["maps_ready"])
        self.assertFalse(status["maps_issues"])
        self.autogrid_file.write_bytes(b"autogrid-v1")

    def test_incomplete_autogrid_output_keeps_failed_audit(self) -> None:
        project_dir = self._create_ready_project()

        failed = self._generate(
            project_dir,
            runner=self._runner(omit_name="receptor.HD.map"),
        )

        self.assertFalse(failed["ok"])
        self.assertTrue((project_dir / failed["manifest_file"]).is_file())
        self.assertNotIn(
            "active_maps_manifest",
            self._project_data(project_dir)["hydrated_docking"],
        )
        status = self._status(project_dir)
        self.assertEqual(status["maps_status"], "not_prepared")

    def test_status_rejects_maps_pointer_outside_hydrated_record(self) -> None:
        project_dir = self._create_ready_project()
        outside = project_dir / "maps" / "other.json"
        outside.parent.mkdir(exist_ok=True)
        outside.write_text("{}\n", encoding="utf-8")
        payload = self._project_data(project_dir)
        payload["hydrated_docking"]["active_maps_manifest"] = {
            "path": "maps/other.json",
            "sha256": "0" * 64,
        }
        (project_dir / "project.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        status = self._status(project_dir)

        self.assertEqual(status["maps_status"], "invalid")
        self.assertFalse(status["maps_ready"])
        self.assertTrue(status["maps_issues"])


if __name__ == "__main__":
    unittest.main()
