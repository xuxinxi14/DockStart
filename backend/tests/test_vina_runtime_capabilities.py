from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core.autogrid import generate_maps  # noqa: E402
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    _validate_execute_prerequisites,
    create_project,
    execute_prepared_vina_run,
    generate_vina_config,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    prepare_vina_run,
    update_box_params,
    update_vina_params,
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


class VinaRuntimeCapabilityGateTests(unittest.TestCase):
    @staticmethod
    def _capabilities(
        *,
        autobox: str = "supported",
        no_refine: str = "supported",
        force_even_voxels: str = "supported",
        unbound_energy: str = "supported",
    ) -> dict[str, object]:
        def feature(key: str, status: str) -> dict[str, object]:
            minimum = (
                "1.2.3"
                if key == "autobox"
                else (
                    "1.2.4"
                    if key in {"no_refine", "unbound_energy"}
                    else "1.2.0"
                )
            )
            supported = True if status == "supported" else False if status == "unsupported" else None
            advertised = True if status == "supported" else False if status == "unsupported" else None
            return {
                "option": f"--{key}",
                "status": status,
                "supported": supported,
                "advertised": advertised,
                "minimum_version": minimum,
                "version_compatible": True,
                "message": f"mock {key}: {status}",
            }

        statuses = {autobox, no_refine, force_even_voxels, unbound_energy}
        return {
            "status": "ok" if statuses == {"supported"} else "partial",
            "source": "help_advanced",
            "checked": True,
            "version": "1.2.7",
            "help_exit_code": 0,
            "help_sha256": "0" * 64,
            "features": {
                "autobox": feature("autobox", autobox),
                "no_refine": feature("no_refine", no_refine),
                "force_even_voxels": feature(
                    "force_even_voxels",
                    force_even_voxels,
                ),
                "unbound_energy": feature(
                    "unbound_energy",
                    unbound_energy,
                ),
            },
            "message": "mock capability profile",
            "raw_error": "",
        }

    def _vina_result(
        self,
        executable: Path,
        *,
        autobox: str = "supported",
        no_refine: str = "supported",
        force_even_voxels: str = "supported",
        unbound_energy: str = "supported",
    ) -> ToolCheckResult:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version="1.2.7",
            path=str(executable),
            message="已检测到 AutoDock Vina。",
            source="configured",
            capabilities=self._capabilities(
                autobox=autobox,
                no_refine=no_refine,
                force_even_voxels=force_even_voxels,
                unbound_energy=unbound_energy,
            ),
        )

    def _create_project(self, temp_dir: str) -> Path:
        created = create_project("runtime_gate", temp_dir)
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])
        receptor = Path(temp_dir) / "receptor.pdbqt"
        ligand = Path(temp_dir) / "ligand.pdbqt"
        receptor.write_text(RECEPTOR_PDBQT, encoding="utf-8")
        ligand.write_text(LIGAND_PDBQT, encoding="utf-8")
        self.assertTrue(
            import_receptor_pdbqt(str(project_dir), str(receptor))["ok"],
        )
        self.assertTrue(
            import_ligand_pdbqt(str(project_dir), str(ligand))["ok"],
        )
        return project_dir

    def _prepare(
        self,
        temp_dir: str,
        *,
        vina_updates: dict[str, object] | None = None,
        run_mode: str = "dock",
    ) -> tuple[Path, Path, dict[str, object]]:
        project_dir = self._create_project(temp_dir)
        if vina_updates:
            updated = update_vina_params(str(project_dir), vina_updates)
            self.assertTrue(updated["ok"], updated)
        if run_mode != "dock":
            protocol = update_vina_run_protocol(
                str(project_dir),
                run_mode,
                True,
                True,
            )
            self.assertTrue(protocol["ok"], protocol)
        generated = generate_vina_config(str(project_dir))
        self.assertTrue(generated["ok"], generated)
        executable = Path(temp_dir) / "vina.exe"
        executable.write_bytes(b"mock vina binary")
        with patch(
            "dockstart_core.project.vina_adapter.detect",
            return_value=self._vina_result(executable),
        ):
            prepared = prepare_vina_run(str(project_dir))
        self.assertTrue(prepared["ok"], prepared)
        return project_dir, executable, prepared

    def test_legacy_run_config_without_expert_options_defaults_both_to_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, _executable, prepared = self._prepare(temp_dir)
            config_path = (
                project_dir
                / "runs"
                / str(prepared["run_id"])
                / "config_snapshot.txt"
            )
            config_text = config_path.read_text(encoding="utf-8")
            self.assertNotIn("no_refine", config_text)
            self.assertNotIn("force_even_voxels", config_text)
            self.assertNotIn("unbound_energy", config_text)

            prerequisites = _validate_execute_prerequisites(
                str(project_dir),
                str(prepared["run_id"]),
                prepared["metadata"],
            )

            self.assertTrue(prerequisites["ok"], prerequisites)
            self.assertEqual(
                prerequisites["effective_vina_options"],
                {
                    "no_refine": False,
                    "force_even_voxels": False,
                    "unbound_energy": None,
                },
            )

    def test_execution_rechecks_frozen_options_before_spawning_vina(self) -> None:
        cases = (
            (
                "autobox",
                {},
                {"autobox": "unsupported"},
            ),
            (
                "no_refine",
                {"no_refine": True},
                {"no_refine": "unsupported"},
            ),
            (
                "force_even_voxels",
                {"force_even_voxels": True},
                {"force_even_voxels": "unknown"},
            ),
            (
                "unbound_energy",
                {"unbound_energy": 5},
                {"unbound_energy": "unsupported"},
            ),
        )
        for label, vina_updates, execution_profile in cases:
            with self.subTest(feature=label), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, executable, prepared = self._prepare(
                    temp_dir,
                    vina_updates=vina_updates,
                    run_mode=(
                        "score_only"
                        if label in {"autobox", "unbound_energy"}
                        else "dock"
                    ),
                )
                execution_detection = self._vina_result(
                    executable,
                    **execution_profile,
                )
                with (
                    patch(
                        "dockstart_core.project.vina_adapter.detect",
                        return_value=execution_detection,
                    ),
                    patch(
                        "dockstart_core.project.vina_adapter.run_managed",
                    ) as run_mock,
                ):
                    executed = execute_prepared_vina_run(
                        str(project_dir),
                        str(prepared["run_id"]),
                    )

                self.assertFalse(executed["ok"], executed)
                self.assertEqual(
                    executed["error"]["code"],
                    "RUN_VINA_CAPABILITY_MISMATCH",
                )
                run_mock.assert_not_called()
                metadata = json.loads(
                    (
                        project_dir
                        / "runs"
                        / str(prepared["run_id"])
                        / "metadata.json"
                    ).read_text(encoding="utf-8"),
                )
                self.assertEqual(metadata["status"], "prepared")
                self.assertFalse(
                    (
                        project_dir
                        / "runs"
                        / str(prepared["run_id"])
                        / "stdout.txt"
                    ).exists(),
                )

    def test_effective_config_rejects_duplicate_and_invalid_booleans(self) -> None:
        cases = (
            (
                "duplicate",
                "no_refine = true\nno_refine = false\n",
            ),
            (
                "invalid",
                "force_even_voxels = yes\n",
            ),
        )
        for label, extra_config in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, _executable, prepared = self._prepare(temp_dir)
                run_id = str(prepared["run_id"])
                config_path = project_dir / "runs" / run_id / "config_snapshot.txt"
                config_path.write_text(
                    config_path.read_text(encoding="utf-8") + extra_config,
                    encoding="utf-8",
                )
                metadata = prepared["metadata"]
                metadata["snapshots"]["config"]["sha256"] = hashlib.sha256(
                    config_path.read_bytes(),
                ).hexdigest()

                prerequisites = _validate_execute_prerequisites(
                    str(project_dir),
                    run_id,
                    metadata,
                )

                self.assertFalse(prerequisites["ok"], prerequisites)
                self.assertEqual(
                    prerequisites["error"]["code"],
                    "RUN_CONFIG_ADVANCED_OPTION_INVALID",
                )

    def test_score_only_freezes_explicit_unbound_energy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, _executable, prepared = self._prepare(
                temp_dir,
                vina_updates={"unbound_energy": 0},
                run_mode="score_only",
            )
            run_id = str(prepared["run_id"])
            config_path = project_dir / "runs" / run_id / "config_snapshot.txt"
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("unbound_energy = 0", config_text)

            prerequisites = _validate_execute_prerequisites(
                str(project_dir),
                run_id,
                prepared["metadata"],
            )

            self.assertTrue(prerequisites["ok"], prerequisites)
            self.assertEqual(
                prerequisites["effective_vina_options"]["unbound_energy"],
                0.0,
            )

    def test_frozen_unbound_energy_rejects_invalid_or_duplicate_values(self) -> None:
        cases = (
            ("duplicate", "unbound_energy = 2\n"),
            ("nan", "unbound_energy = nan\n"),
            ("infinity", "unbound_energy = inf\n"),
        )
        for label, extra_config in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, _executable, prepared = self._prepare(
                    temp_dir,
                    vina_updates={"unbound_energy": 1},
                    run_mode="score_only",
                )
                run_id = str(prepared["run_id"])
                config_path = project_dir / "runs" / run_id / "config_snapshot.txt"
                config_path.write_text(
                    config_path.read_text(encoding="utf-8") + extra_config,
                    encoding="utf-8",
                )
                metadata = prepared["metadata"]
                metadata["snapshots"]["config"]["sha256"] = hashlib.sha256(
                    config_path.read_bytes(),
                ).hexdigest()

                prerequisites = _validate_execute_prerequisites(
                    str(project_dir),
                    run_id,
                    metadata,
                )

                self.assertFalse(prerequisites["ok"], prerequisites)
                self.assertEqual(
                    prerequisites["error"]["code"],
                    "RUN_CONFIG_UNBOUND_ENERGY_INVALID",
                )

    def test_unbound_energy_in_non_score_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, _executable, prepared = self._prepare(temp_dir)
            run_id = str(prepared["run_id"])
            config_path = project_dir / "runs" / run_id / "config_snapshot.txt"
            config_path.write_text(
                config_path.read_text(encoding="utf-8")
                + "unbound_energy = 5\n",
                encoding="utf-8",
            )
            metadata = prepared["metadata"]
            metadata["snapshots"]["config"]["sha256"] = hashlib.sha256(
                config_path.read_bytes(),
            ).hexdigest()

            prerequisites = _validate_execute_prerequisites(
                str(project_dir),
                run_id,
                metadata,
            )

            self.assertFalse(prerequisites["ok"], prerequisites)
            self.assertEqual(
                prerequisites["error"]["code"],
                "RUN_CONFIG_UNBOUND_ENERGY_NOT_APPLICABLE",
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

    def test_ad4_maps_ignores_vina_grid_expert_option_capability_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
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
                )["ok"],
            )
            updated = update_vina_params(
                str(project_dir),
                {
                    "no_refine": True,
                    "force_even_voxels": True,
                    "unbound_energy": 5,
                },
            )
            self.assertTrue(updated["ok"], updated)
            autogrid = Path(temp_dir) / "autogrid4.exe"
            autogrid.write_bytes(b"mock autogrid binary")
            autogrid_detection = ToolCheckResult(
                key="autogrid4",
                name="AutoGrid4",
                status="ok",
                version="4.2.6",
                path=str(autogrid),
                message="已检测到 AutoGrid4。",
                source="configured",
            )
            with patch(
                "dockstart_core.autogrid.autogrid_adapter.detect",
                return_value=autogrid_detection,
            ):
                generated_maps = generate_maps(
                    str(project_dir),
                    runner=self._fake_autogrid,
                )
            self.assertTrue(generated_maps["ok"], generated_maps)
            generated_config = generate_vina_config(str(project_dir))
            self.assertTrue(generated_config["ok"], generated_config)
            self.assertNotIn("no_refine", generated_config["config_text"])
            self.assertNotIn(
                "force_even_voxels",
                generated_config["config_text"],
            )
            self.assertNotIn("unbound_energy", generated_config["config_text"])

            vina = Path(temp_dir) / "vina.exe"
            vina.write_bytes(b"mock vina binary")
            unknown_detection = self._vina_result(
                vina,
                no_refine="unknown",
                force_even_voxels="unsupported",
                unbound_energy="unsupported",
            )
            with patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=unknown_detection,
            ):
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)
            self.assertEqual(
                prepared["metadata"]["scoring_protocol"],
                "ad4_maps",
            )

            def fake_run(
                command: list[str],
                cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **_kwargs: object,
            ) -> ManagedRunResult:
                Path(stdout_path).write_text("mock vina stdout\n", encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text("mock vina log\n", encoding="utf-8")
                output = Path(cwd) / command[command.index("--out") + 1]
                output.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
                return ManagedRunResult(pid=4242, exit_code=0)

            executor_identity = {
                "pid": os.getpid(),
                "executable_path": sys.executable,
                "creation_token": "runtime-capability-test",
            }
            with (
                patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=unknown_detection,
                ),
                patch(
                    "dockstart_core.project.vina_adapter.get_process_identity",
                    return_value=executor_identity,
                ),
                patch(
                    "dockstart_core.project.vina_adapter.run_managed",
                    side_effect=fake_run,
                ) as run_mock,
            ):
                executed = execute_prepared_vina_run(
                    str(project_dir),
                    str(prepared["run_id"]),
                )

            self.assertTrue(executed["ok"], executed)
            run_mock.assert_called_once()
            command = run_mock.call_args.args[0]
            self.assertIn("--maps", command)
            self.assertNotIn("--no_refine", command)
            self.assertNotIn("--force_even_voxels", command)


if __name__ == "__main__":
    unittest.main()
