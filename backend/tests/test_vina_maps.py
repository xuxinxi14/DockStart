from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import vina_adapter  # noqa: E402
from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    create_project,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    update_box_params,
    update_vina_params,
)
from dockstart_core.vina_maps import (  # noqa: E402
    RAW_ATTESTATION_STATEMENT,
    generate_maps,
    import_maps,
    set_grid_source,
    validate_active_maps,
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


class VinaMapsWorkflowTests(unittest.TestCase):
    @staticmethod
    def _capabilities() -> dict[str, object]:
        def feature(key: str) -> dict[str, object]:
            return {
                "option": f"--{key}",
                "status": "supported",
                "supported": True,
                "advertised": True,
                "minimum_version": "1.2.0",
                "version_compatible": True,
                "message": f"mock {key}",
            }

        keys = (
            "maps",
            "write_maps",
            "no_refine",
            "force_even_voxels",
        )
        return {
            "status": "ok",
            "source": "help_advanced",
            "checked": True,
            "version": "1.2.7",
            "features": {key: feature(key) for key in keys},
        }

    def _vina_result(self, executable: Path) -> ToolCheckResult:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version="1.2.7",
            path=str(executable),
            message="mock Vina",
            source="configured",
            capabilities=self._capabilities(),
        )

    def _create_project(self, base_dir: str, name: str = "vina_maps_demo") -> Path:
        created = create_project(name, base_dir)
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])
        receptor = Path(base_dir) / f"{name}_receptor.pdbqt"
        ligand = Path(base_dir) / f"{name}_ligand.pdbqt"
        receptor.write_text(RECEPTOR_PDBQT, encoding="utf-8")
        ligand.write_text(LIGAND_PDBQT, encoding="utf-8")
        self.assertTrue(
            import_receptor_pdbqt(str(project_dir), str(receptor))["ok"]
        )
        self.assertTrue(
            import_ligand_pdbqt(str(project_dir), str(ligand))["ok"]
        )
        updated = update_box_params(
            str(project_dir),
            {
                "center_x": 1.23456789,
                "center_y": 2.1234567,
                "center_z": 3.9876543,
                "size_x": 1.0,
                "size_y": 1.1,
                "size_z": 1.6,
            },
        )
        self.assertTrue(updated["ok"], updated)
        return project_dir

    @staticmethod
    def _argument(command: list[str], option: str) -> str:
        return command[command.index(option) + 1]

    @classmethod
    def _write_maps(cls, command: list[str], cwd: Path) -> None:
        spacing = float(cls._argument(command, "--spacing"))
        center = [
            float(cls._argument(command, f"--center_{axis}"))
            for axis in ("x", "y", "z")
        ]
        sizes = [
            float(cls._argument(command, f"--size_{axis}"))
            for axis in ("x", "y", "z")
        ]
        nelements = []
        for size in sizes:
            count = math.ceil(size / spacing)
            if count % 2:
                count += 1
            nelements.append(count)
        value_count = math.prod(value + 1 for value in nelements)
        prefix = cls._argument(command, "--write_maps")
        for atom_type in ("C_H", "N_A"):
            path = cwd / f"{prefix}.{atom_type}.map"
            header = "\n".join(
                (
                    "GRID_PARAMETER_FILE generated.gpf",
                    "GRID_DATA_FILE generated.maps.fld",
                    "MACROMOLECULE inputs/receptor.pdbqt",
                    f"SPACING {spacing}",
                    f"NELEMENTS {' '.join(map(str, nelements))}",
                    f"CENTER {' '.join(f'{value:.6g}' for value in center)}",
                )
            )
            values = "\n".join("0.0" for _ in range(value_count))
            path.write_text(f"{header}\n{values}\n", encoding="ascii")

    @classmethod
    def _runner(
        cls,
        command: list[str],
        cwd: str | Path,
        stdout_path: str | Path,
        stderr_path: str | Path,
        log_path: str | Path,
    ) -> dict[str, object]:
        working_dir = Path(cwd)
        if "--write_maps" in command:
            cls._write_maps(command, working_dir)
        Path(stdout_path).write_text("Affinity: -7.0\n", encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        Path(log_path).write_text("Affinity: -7.0\n", encoding="utf-8")
        return {
            "ok": True,
            "command": command,
            "pid": 123,
            "exit_code": 0,
            "error": "",
        }

    @classmethod
    def _probe_fails(
        cls,
        command: list[str],
        cwd: str | Path,
        stdout_path: str | Path,
        stderr_path: str | Path,
        log_path: str | Path,
    ) -> dict[str, object]:
        if "--write_maps" in command:
            return cls._runner(
                command,
                cwd,
                stdout_path,
                stderr_path,
                log_path,
            )
        Path(stdout_path).write_text("", encoding="utf-8")
        Path(stderr_path).write_text("missing atom type\n", encoding="utf-8")
        Path(log_path).write_text("", encoding="utf-8")
        return {
            "ok": False,
            "command": command,
            "pid": 124,
            "exit_code": 1,
            "error": "missing atom type",
        }

    def test_generate_binds_requested_box_and_preserves_force_even_actual_size(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            executable = Path(temp_dir) / "vina.exe"
            executable.write_bytes(b"mock vina 1.2.7")
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=self._vina_result(executable),
            ):
                generated = generate_maps(
                    str(project_dir),
                    runner=self._runner,
                )
                self.assertTrue(generated["ok"], generated)
                status = validate_active_maps(
                    str(project_dir),
                    runner=self._runner,
                )

            manifest = generated["manifest"]
            self.assertEqual(
                manifest["grid"]["requested_box"]["size"],
                {"x": 1.0, "y": 1.1, "z": 1.6},
            )
            self.assertEqual(
                manifest["grid"]["nelements"],
                {"x": 4, "y": 4, "z": 6},
            )
            self.assertEqual(
                manifest["grid"]["actual_size"],
                {"x": 1.5, "y": 1.5, "z": 2.25},
            )
            self.assertEqual(
                generated["project"]["box"]["size_x"],
                1.0,
            )
            self.assertIn("--no_refine", manifest["vina"]["command"])
            self.assertTrue(status["ok"], status)
            self.assertTrue(status["ready"])
            self.assertTrue(status["protocol_active"])
            self.assertEqual(
                status["current_context"]["grid"]["requested_box"]["size"]["z"],
                1.6,
            )

    def test_map_payload_corruption_invalidates_active_maps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            executable = Path(temp_dir) / "vina.exe"
            executable.write_bytes(b"mock vina 1.2.7")
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=self._vina_result(executable),
            ):
                generated = generate_maps(str(project_dir), runner=self._runner)
                self.assertTrue(generated["ok"], generated)
                first_map = (
                    project_dir
                    / "maps"
                    / generated["map_set_id"]
                    / generated["manifest"]["maps"]["files"][0]["name"]
                )
                first_map.write_text(
                    first_map.read_text(encoding="ascii") + "0.0\n",
                    encoding="ascii",
                )
                corrupted = validate_active_maps(
                    str(project_dir),
                    runner=self._runner,
                )
            self.assertFalse(corrupted["ok"])
            self.assertEqual(
                corrupted["error"]["code"],
                "VINA_MAPS_VALIDATION_FAILED",
            )

    def test_receptor_scoring_and_vina_binary_are_exact_bindings(self) -> None:
        mutations = ("receptor", "scoring", "vina_binary")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp_dir:
                project_dir = self._create_project(temp_dir)
                executable = Path(temp_dir) / "vina.exe"
                executable.write_bytes(b"mock vina 1.2.7")
                detection = self._vina_result(executable)
                with patch(
                    "dockstart_core.vina_maps.vina_adapter.detect",
                    return_value=detection,
                ):
                    generated = generate_maps(
                        str(project_dir),
                        runner=self._runner,
                    )
                    self.assertTrue(generated["ok"], generated)
                    if mutation == "receptor":
                        receptor = project_dir / "prepared" / "receptor.pdbqt"
                        receptor.write_text(
                            receptor.read_text(encoding="utf-8")
                            + "REMARK changed\n",
                            encoding="utf-8",
                        )
                    elif mutation == "scoring":
                        updated = update_vina_params(
                            str(project_dir),
                            {"scoring": "vinardo"},
                        )
                        self.assertTrue(updated["ok"], updated)
                    else:
                        executable.write_bytes(b"different vina binary")
                    status = validate_active_maps(
                        str(project_dir),
                        runner=self._runner,
                    )
                self.assertFalse(status["ok"], status)
                self.assertEqual(
                    status["error"]["code"],
                    "VINA_MAPS_VALIDATION_FAILED",
                )

    def test_raw_import_requires_exact_attestation_and_runs_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            executable = Path(temp_dir) / "vina.exe"
            executable.write_bytes(b"mock vina 1.2.7")
            detection = self._vina_result(executable)
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=detection,
            ):
                generated = generate_maps(str(project_dir), runner=self._runner)
                self.assertTrue(generated["ok"], generated)
                source_dir = project_dir / "maps" / generated["map_set_id"]
                rejected = import_maps(
                    str(project_dir),
                    str(source_dir),
                    runner=self._runner,
                )
                self.assertFalse(rejected["ok"])
                self.assertEqual(
                    rejected["error"]["code"],
                    "VINA_MAPS_IMPORT_ATTESTATION_REQUIRED",
                )
                context = validate_active_maps(
                    str(project_dir),
                    probe_ligand=False,
                    runner=self._runner,
                )["current_context"]
                template = context["raw_import_attestation_template"]
                attestation = {
                    **template,
                    "confirmed": True,
                    "statement": RAW_ATTESTATION_STATEMENT,
                }
                imported = import_maps(
                    str(project_dir),
                    str(source_dir),
                    {"attestation": attestation},
                    runner=self._runner,
                )
            self.assertTrue(imported["ok"], imported)
            self.assertEqual(
                imported["manifest"]["source"],
                "imported_raw_attested",
            )
            self.assertTrue(imported["compatibility_probe"]["ok"])

    def test_switch_to_receptor_keeps_valid_asset_reactivatable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            executable = Path(temp_dir) / "vina.exe"
            executable.write_bytes(b"mock vina 1.2.7")
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=self._vina_result(executable),
            ):
                generated = generate_maps(str(project_dir), runner=self._runner)
                switched = set_grid_source(str(project_dir), "receptor")
                self.assertTrue(switched["ok"], switched)
                inactive = validate_active_maps(
                    str(project_dir),
                    runner=self._runner,
                )
                reactivated = set_grid_source(
                    str(project_dir),
                    "precomputed_maps",
                    generated["map_set_id"],
                    runner=self._runner,
                )
            self.assertTrue(inactive["ok"], inactive)
            self.assertTrue(inactive["ready"])
            self.assertFalse(inactive["protocol_active"])
            self.assertEqual(
                inactive["manifest_file"],
                generated["manifest_file"],
            )
            self.assertTrue(reactivated["ok"], reactivated)

    def test_probe_failure_blocks_activation_but_keeps_ready_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            executable = Path(temp_dir) / "vina.exe"
            executable.write_bytes(b"mock vina 1.2.7")
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=self._vina_result(executable),
            ):
                generated = generate_maps(
                    str(project_dir),
                    runner=self._probe_fails,
                )
            self.assertFalse(generated["ok"])
            self.assertEqual(
                generated["error"]["code"],
                "VINA_MAPS_LIGAND_PROBE_FAILED",
            )
            manifest = json.loads(
                (
                    project_dir
                    / generated["manifest_file"]
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "ready")

    def test_dockstart_manifest_import_revalidates_all_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "vina.exe"
            executable.write_bytes(b"mock vina 1.2.7")
            detection = self._vina_result(executable)
            source_project = self._create_project(temp_dir, "source_project")
            target_project = self._create_project(temp_dir, "target_project")
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=detection,
            ):
                generated = generate_maps(
                    str(source_project),
                    {"activate": False},
                    runner=self._runner,
                )
                self.assertTrue(generated["ok"], generated)
                imported = import_maps(
                    str(target_project),
                    str(source_project / generated["manifest_file"]),
                    runner=self._runner,
                )
            self.assertTrue(imported["ok"], imported)
            self.assertEqual(
                imported["manifest"]["source"],
                "imported_manifest",
            )
            self.assertEqual(
                imported["manifest"]["provenance"]["source_map_set_id"],
                generated["map_set_id"],
            )
            self.assertRegex(
                imported["manifest"]["provenance"]["source_manifest_sha256"],
                r"^[0-9a-f]{64}$",
            )

    def test_adapter_run_command_delegates_to_managed_process_boundary(self) -> None:
        command = ["vina.exe", "--score_only"]
        with patch.object(
            vina_adapter,
            "run_managed",
            return_value=ManagedRunResult(pid=42, exit_code=0, error=""),
        ) as managed:
            result = vina_adapter.run_command(
                command,
                "C:/work",
                "stdout.txt",
                "stderr.txt",
                "log.txt",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["command"], command)
        self.assertEqual(result["pid"], 42)
        managed.assert_called_once_with(
            command,
            "C:/work",
            "stdout.txt",
            "stderr.txt",
            "log.txt",
        )


if __name__ == "__main__":
    unittest.main()
