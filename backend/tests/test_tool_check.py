from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import autogrid_adapter, meeko_adapter, python_adapter, rdkit_adapter, vina_adapter  # noqa: E402
from adapters.python_adapter import detect as detect_python  # noqa: E402
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.settings import (  # noqa: E402
    SETTINGS_ENV_VAR,
    DockStartSettings,
    ToolPaths,
    get_settings_path,
    load_settings,
    save_settings,
)
from dockstart_core.toolchain import get_toolchain_status  # noqa: E402
from dockstart_core.toolchain import get_resolved_python  # noqa: E402
from dockstart_core.toolchain_paths import (  # noqa: E402
    RESOURCE_DIR_ENV_VAR,
    TOOLCHAIN_ROOT_ENV_VAR,
    get_bundled_python_path,
    get_bundled_vina_path,
    get_legacy_bundled_vina_path,
    get_licenses_dir,
    get_runtime_mode,
    get_toolchain_manifest_path,
    get_toolchain_root,
)


class ToolCheckTests(unittest.TestCase):
    def test_tool_check_result_serializes(self) -> None:
        result = ToolCheckResult(
            key="example",
            name="示例工具",
            status="unknown",
            message="用于测试序列化。",
            source="unknown",
        )

        payload = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))

        self.assertEqual(payload["key"], "example")
        self.assertEqual(payload["status"], "unknown")
        self.assertEqual(payload["raw_error"], "")
        self.assertEqual(payload["source"], "unknown")
        self.assertEqual(payload["bundled_path"], "")
        self.assertFalse(payload["is_bundled"])
        self.assertEqual(payload["capabilities"], {})

    def test_settings_loads_default_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "missing_settings.json"
            with patch.dict(os.environ, {SETTINGS_ENV_VAR: str(settings_path)}):
                settings = load_settings()

        self.assertEqual(settings.tool_paths.vina, "")
        self.assertEqual(settings.tool_paths.python, "")
        self.assertEqual(settings.tool_paths.autogrid4, "")
        self.assertEqual(settings.project.default_project_dir, "")

    def test_settings_saves_and_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "dockstart_settings.json"
            expected = DockStartSettings(
                tool_paths=ToolPaths(
                    vina="C:/tools/vina.exe",
                    python="C:/Python/python.exe",
                    autogrid4="C:/tools/autogrid4.exe",
                ),
            )
            with patch.dict(os.environ, {SETTINGS_ENV_VAR: str(settings_path)}):
                save_settings(expected)
                loaded = load_settings()
                resolved_path = get_settings_path()

        self.assertEqual(loaded.tool_paths.vina, "C:/tools/vina.exe")
        self.assertEqual(loaded.tool_paths.python, "C:/Python/python.exe")
        self.assertEqual(loaded.tool_paths.autogrid4, "C:/tools/autogrid4.exe")
        self.assertEqual(resolved_path, settings_path)

    def test_autogrid_detection_is_external_and_structured(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout="AutoGrid 4.2.6\n",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "autogrid4.exe"
            executable.write_bytes(b"mock")
            with patch.object(autogrid_adapter.subprocess, "run", return_value=completed) as run_mock:
                result = autogrid_adapter.detect(str(executable))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.version, "4.2.6")
        self.assertEqual(result.source, "configured")
        self.assertFalse(result.is_bundled)
        self.assertEqual(run_mock.call_args[0][0][0], str(executable.resolve()))

    def test_python_detection_returns_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_bundled = Path(temp_dir) / "resources" / "python" / "python.exe"
            result = detect_python(bundled_path=str(missing_bundled))

        self.assertEqual(result.key, "python")
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.version)
        self.assertTrue(result.path)
        self.assertEqual(result.source, "current_environment")

    def test_bundled_python_takes_priority_over_configured(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="Python 3.11.8\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = Path(temp_dir) / "resources" / "python" / "python.exe"
            configured_path = Path(temp_dir) / "configured" / "python.exe"
            bundled_path.parent.mkdir(parents=True)
            configured_path.parent.mkdir(parents=True)
            bundled_path.write_text("fake bundled python", encoding="utf-8")
            configured_path.write_text("fake configured python", encoding="utf-8")

            with patch.object(python_adapter.subprocess, "run", return_value=completed) as run_mock:
                result = python_adapter.detect(str(configured_path), bundled_path=str(bundled_path))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "bundled")
        self.assertTrue(result.is_bundled)
        self.assertEqual(result.path, str(bundled_path.resolve()))
        self.assertEqual(run_mock.call_args[0][0][0], str(bundled_path.resolve()))

    def test_configured_python_takes_priority_over_current_when_no_bundled(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="Python 3.10.11\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = Path(temp_dir) / "resources" / "python" / "python.exe"
            configured_path = Path(temp_dir) / "configured" / "python.exe"
            configured_path.parent.mkdir(parents=True)
            configured_path.write_text("fake configured python", encoding="utf-8")

            with patch.object(python_adapter.subprocess, "run", return_value=completed) as run_mock:
                result = python_adapter.detect(str(configured_path), bundled_path=str(bundled_path))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "configured")
        self.assertFalse(result.is_bundled)
        self.assertEqual(result.path, str(configured_path))
        self.assertEqual(run_mock.call_args[0][0][0], str(configured_path))

    def test_configured_python_can_be_preferred_over_bundled_for_preparation_tools(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="Python 3.11.15\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = Path(temp_dir) / "resources" / "python" / "python.exe"
            configured_path = Path(temp_dir) / "configured" / "python.exe"
            bundled_path.parent.mkdir(parents=True)
            configured_path.parent.mkdir(parents=True)
            bundled_path.write_text("fake bundled python", encoding="utf-8")
            configured_path.write_text("fake configured python", encoding="utf-8")

            with patch.object(python_adapter.subprocess, "run", return_value=completed) as run_mock:
                result = python_adapter.detect(
                    str(configured_path),
                    bundled_path=str(bundled_path),
                    prefer_configured=True,
                )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "configured")
        self.assertFalse(result.is_bundled)
        self.assertEqual(result.path, str(configured_path))
        self.assertEqual(run_mock.call_args[0][0][0], str(configured_path))

    def test_prefer_configured_python_falls_back_to_bundled_when_configured_missing(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="Python 3.11.15\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = Path(temp_dir) / "resources" / "python" / "python.exe"
            configured_path = Path(temp_dir) / "configured" / "missing-python.exe"
            bundled_path.parent.mkdir(parents=True)
            bundled_path.write_text("fake bundled python", encoding="utf-8")

            with patch.object(python_adapter.subprocess, "run", return_value=completed) as run_mock:
                result = python_adapter.detect(
                    str(configured_path),
                    bundled_path=str(bundled_path),
                    prefer_configured=True,
                )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "bundled")
        self.assertTrue(result.is_bundled)
        self.assertEqual(result.path, str(bundled_path.resolve()))
        self.assertEqual(run_mock.call_args[0][0][0], str(bundled_path.resolve()))

    def test_meeko_and_rdkit_use_resolved_bundled_python(self) -> None:
        python_completed = SimpleNamespace(returncode=0, stdout="Python 3.11.8\n", stderr="")
        import_completed = SimpleNamespace(returncode=0, stdout="1.0.0\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_path = root / "resources" / "python" / "python.exe"
            bundled_path.parent.mkdir(parents=True)
            bundled_path.write_text("fake bundled python", encoding="utf-8")
            settings_path = root / "settings.json"

            with (
                patch.dict(
                    os.environ,
                    {
                        TOOLCHAIN_ROOT_ENV_VAR: str(root),
                        SETTINGS_ENV_VAR: str(settings_path),
                    },
                ),
                patch.object(python_adapter.subprocess, "run", return_value=python_completed),
            ):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
                resolved = get_resolved_python("")

            with patch.object(meeko_adapter.subprocess, "run", return_value=import_completed) as meeko_run:
                meeko_result = meeko_adapter.detect(resolved.path, resolved.source)
            with patch.object(rdkit_adapter.subprocess, "run", return_value=import_completed) as rdkit_run:
                rdkit_result = rdkit_adapter.detect(resolved.path, resolved.source)

        self.assertEqual(resolved.source, "bundled")
        self.assertEqual(meeko_result.source, "bundled")
        self.assertEqual(rdkit_result.source, "bundled")
        self.assertEqual(meeko_run.call_args[0][0][0], str(bundled_path.resolve()))
        self.assertEqual(rdkit_run.call_args[0][0][0], str(bundled_path.resolve()))

    def test_scientific_import_probes_allow_slow_assisted_cold_start(self) -> None:
        import_completed = SimpleNamespace(returncode=0, stdout="0.7.1\n", stderr="")
        capability_completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "import_available": True,
                    "version": "0.7.1",
                    "capabilities": {"import": {"status": "ok", "message": "可导入"}},
                },
            ),
            stderr="",
        )

        with patch.object(meeko_adapter.subprocess, "run", return_value=import_completed) as meeko_run:
            meeko_adapter.detect(sys.executable, "configured")
        with patch.object(rdkit_adapter.subprocess, "run", return_value=import_completed) as rdkit_run:
            rdkit_adapter.detect(sys.executable, "configured")
        with patch.object(meeko_adapter.subprocess, "run", return_value=capability_completed) as meeko_capability_run:
            meeko_adapter.detect_meeko_capabilities(sys.executable, "configured")
        with patch.object(rdkit_adapter.subprocess, "run", return_value=capability_completed) as rdkit_capability_run:
            rdkit_adapter.detect_rdkit_capabilities(sys.executable, "configured")

        for run_mock in (meeko_run, rdkit_run, meeko_capability_run, rdkit_capability_run):
            self.assertGreaterEqual(run_mock.call_args.kwargs["timeout"], 30)

    def test_meeko_and_rdkit_use_configured_python_when_user_sets_one(self) -> None:
        python_completed = SimpleNamespace(returncode=0, stdout="Python 3.11.15\n", stderr="")
        import_completed = SimpleNamespace(returncode=0, stdout="1.0.0\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_path = root / "resources" / "python" / "python.exe"
            configured_path = root / "configured" / "python.exe"
            bundled_path.parent.mkdir(parents=True)
            configured_path.parent.mkdir(parents=True)
            bundled_path.write_text("fake bundled python", encoding="utf-8")
            configured_path.write_text("fake configured python", encoding="utf-8")
            settings_path = root / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "tool_paths": {
                            "vina": "",
                            "python": str(configured_path),
                        },
                        "project": {"default_project_dir": ""},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with (
                patch.dict(
                    os.environ,
                    {
                        TOOLCHAIN_ROOT_ENV_VAR: str(root),
                        SETTINGS_ENV_VAR: str(settings_path),
                    },
                ),
                patch.object(python_adapter.subprocess, "run", return_value=python_completed),
            ):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
                resolved = get_resolved_python()

            with patch.object(meeko_adapter.subprocess, "run", return_value=import_completed) as meeko_run:
                meeko_result = meeko_adapter.detect(resolved.path, resolved.source)
            with patch.object(rdkit_adapter.subprocess, "run", return_value=import_completed) as rdkit_run:
                rdkit_result = rdkit_adapter.detect(resolved.path, resolved.source)

        self.assertEqual(resolved.source, "configured")
        self.assertEqual(meeko_result.source, "configured")
        self.assertEqual(rdkit_result.source, "configured")
        self.assertEqual(meeko_run.call_args[0][0][0], str(configured_path))
        self.assertEqual(rdkit_run.call_args[0][0][0], str(configured_path))

    def test_vina_missing_returns_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = str(Path(temp_dir) / "resources" / "vina" / "vina.exe")
            with patch.object(vina_adapter.shutil, "which", return_value=None):
                result = vina_adapter.detect(bundled_path=bundled_path)

        self.assertEqual(result.key, "vina")
        self.assertIn(result.status, {"missing", "error"})
        self.assertEqual(result.source, "missing")
        self.assertEqual(result.bundled_path, bundled_path)

    def test_configured_vina_missing_returns_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = str(Path(temp_dir) / "resources" / "vina" / "vina.exe")
            result = vina_adapter.detect("Z:/missing/vina.exe", bundled_path=bundled_path)

        self.assertEqual(result.key, "vina")
        self.assertIn(result.status, {"missing", "error"})
        self.assertEqual(result.source, "configured")
        self.assertIn("用户配置", result.message)

    def test_unconfigured_vina_still_uses_auto_detection(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = str(Path(temp_dir) / "resources" / "vina" / "vina.exe")
            with (
                patch.object(vina_adapter.shutil, "which", return_value="C:/tools/vina.exe"),
                patch.object(vina_adapter.subprocess, "run", return_value=completed),
            ):
                result = vina_adapter.detect("", bundled_path=bundled_path)

        self.assertEqual(result.key, "vina")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "auto")
        self.assertFalse(result.is_bundled)

    def test_bundled_vina_takes_priority_over_configured(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = Path(temp_dir) / "resources" / "vina" / "vina.exe"
            configured_path = Path(temp_dir) / "configured" / "vina.exe"
            bundled_path.parent.mkdir(parents=True)
            configured_path.parent.mkdir(parents=True)
            bundled_path.write_text("fake bundled vina", encoding="utf-8")
            configured_path.write_text("fake configured vina", encoding="utf-8")

            with patch.object(vina_adapter.subprocess, "run", return_value=completed) as run_mock:
                result = vina_adapter.detect(str(configured_path), bundled_path=str(bundled_path))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "bundled")
        self.assertTrue(result.is_bundled)
        self.assertEqual(result.path, str(bundled_path))
        self.assertEqual(run_mock.call_args[0][0][0], str(bundled_path))

    def test_legacy_bundled_vina_is_detected_when_preferred_path_missing(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy_path = root / "resources" / "tools" / "vina" / "vina.exe"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text("fake legacy bundled vina", encoding="utf-8")

            with (
                patch.dict(os.environ, {TOOLCHAIN_ROOT_ENV_VAR: str(root)}, clear=False),
                patch.object(vina_adapter.subprocess, "run", return_value=completed) as run_mock,
            ):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
                result = vina_adapter.detect()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "bundled")
        self.assertEqual(result.path, str(legacy_path))
        self.assertEqual(run_mock.call_args[0][0][0], str(legacy_path))

    def test_configured_vina_takes_priority_over_path_when_no_bundled(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = Path(temp_dir) / "resources" / "vina" / "vina.exe"
            configured_path = Path(temp_dir) / "configured" / "vina.exe"
            configured_path.parent.mkdir(parents=True)
            configured_path.write_text("fake configured vina", encoding="utf-8")

            with (
                patch.object(vina_adapter.shutil, "which", return_value="C:/path/vina.exe"),
                patch.object(vina_adapter.subprocess, "run", return_value=completed) as run_mock,
            ):
                result = vina_adapter.detect(str(configured_path), bundled_path=str(bundled_path))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "configured")
        self.assertEqual(result.path, str(configured_path))
        self.assertEqual(run_mock.call_args[0][0][0], str(configured_path))

    def test_bundled_missing_falls_back_to_configured(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = Path(temp_dir) / "resources" / "vina" / "vina.exe"
            configured_path = Path(temp_dir) / "configured" / "vina.exe"
            configured_path.parent.mkdir(parents=True)
            configured_path.write_text("fake configured vina", encoding="utf-8")

            with patch.object(vina_adapter.subprocess, "run", return_value=completed):
                result = vina_adapter.detect(str(configured_path), bundled_path=str(bundled_path))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "configured")
        self.assertFalse(result.is_bundled)

    def test_vina_capability_probe_matches_only_option_declaration_lines(self) -> None:
        version_completed = SimpleNamespace(
            returncode=0,
            stdout="AutoDock Vina v1.2.7\n",
            stderr="",
        )
        help_completed = SimpleNamespace(
            returncode=0,
            stdout=(
                "Output:\n"
                "  --write_maps arg  output maps. Options --force_even_voxels and "
                "--unbound_energy may be needed\n"
                "Advanced options:\n"
                "  --no_refine       use precalculated grids for final scoring\n"
            ),
            stderr="",
        )

        with patch.object(
            vina_adapter.subprocess,
            "run",
            side_effect=[version_completed, help_completed],
        ):
            result = vina_adapter._run_version_check("C:/tools/vina.exe", "configured")

        features = result.capabilities["features"]
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.capabilities["status"], "partial")
        self.assertEqual(features["no_refine"]["status"], "supported")
        self.assertTrue(features["no_refine"]["advertised"])
        self.assertEqual(features["write_maps"]["status"], "supported")
        self.assertTrue(features["write_maps"]["advertised"])
        self.assertEqual(features["maps"]["status"], "unsupported")
        self.assertFalse(features["maps"]["advertised"])
        self.assertEqual(features["force_even_voxels"]["status"], "unsupported")
        self.assertFalse(features["force_even_voxels"]["advertised"])
        self.assertEqual(features["unbound_energy"]["status"], "unsupported")
        self.assertFalse(features["unbound_energy"]["advertised"])

    def test_vina_detection_rejects_unrelated_executable_identity(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout="Python 3.13.5\n",
            stderr="",
        )

        with patch.object(
            vina_adapter.subprocess,
            "run",
            return_value=completed,
        ) as run_mock:
            result = vina_adapter._run_version_check("C:/tools/not-vina.exe", "configured")

        self.assertEqual(result.status, "error")
        self.assertEqual(result.capabilities, {})
        self.assertEqual(run_mock.call_count, 1)
        self.assertIn("AutoDock Vina", result.message)

    def test_vina_advanced_features_require_safe_minimum_versions(self) -> None:
        version_completed = SimpleNamespace(
            returncode=0,
            stdout="AutoDock Vina v1.2.3\n",
            stderr="",
        )
        help_completed = SimpleNamespace(
            returncode=0,
            stdout=(
                "Input:\n"
                "  --ligand arg      ligand (PDBQT)\n"
                "  --autobox\n"
                "  --no_refine\n"
                "  --force_even_voxels\n"
                "  --unbound_energy arg (=nan)\n"
            ),
            stderr="",
        )

        with patch.object(
            vina_adapter.subprocess,
            "run",
            side_effect=[version_completed, help_completed],
        ):
            result = vina_adapter._run_version_check("C:/tools/vina.exe", "configured")

        no_refine = result.capabilities["features"]["no_refine"]
        autobox = result.capabilities["features"]["autobox"]
        force_even = result.capabilities["features"]["force_even_voxels"]
        unbound_energy = result.capabilities["features"]["unbound_energy"]
        multiple_ligands = result.capabilities["features"]["multiple_ligands"]
        self.assertEqual(multiple_ligands["status"], "supported")
        self.assertTrue(multiple_ligands["advertised"])
        self.assertEqual(multiple_ligands["minimum_version"], "1.2.0")
        self.assertEqual(no_refine["status"], "unsupported")
        self.assertEqual(autobox["status"], "supported")
        self.assertEqual(autobox["minimum_version"], "1.2.3")
        self.assertFalse(no_refine["supported"])
        self.assertTrue(no_refine["advertised"])
        self.assertFalse(no_refine["version_compatible"])
        self.assertEqual(force_even["status"], "supported")
        self.assertTrue(force_even["version_compatible"])
        self.assertEqual(unbound_energy["status"], "unsupported")
        self.assertFalse(unbound_energy["supported"])
        self.assertTrue(unbound_energy["advertised"])
        self.assertEqual(unbound_energy["minimum_version"], "1.2.4")
        self.assertFalse(unbound_energy["version_compatible"])

    def test_vina_multiple_ligands_requires_stable_1_2_0_and_ligand_option(self) -> None:
        cases = (
            ("1.1.2", "  --ligand arg  ligand (PDBQT)\n", "unsupported"),
            ("1.2.0-rc1", "  --ligand arg  ligand (PDBQT)\n", "unsupported"),
            ("1.2.0", "  --ligand arg  ligand (PDBQT)\n", "supported"),
            ("1.2.7", "  --batch arg  batch ligands (PDBQT)\n", "unsupported"),
        )
        for version, help_text, expected_status in cases:
            with self.subTest(version=version, help_text=help_text):
                version_completed = SimpleNamespace(
                    returncode=0,
                    stdout=f"AutoDock Vina v{version}\n",
                    stderr="",
                )
                help_completed = SimpleNamespace(
                    returncode=0,
                    stdout=help_text,
                    stderr="",
                )
                with patch.object(
                    vina_adapter.subprocess,
                    "run",
                    side_effect=[version_completed, help_completed],
                ):
                    result = vina_adapter._run_version_check(
                        "C:/tools/vina.exe",
                        "configured",
                    )

                feature = result.capabilities["features"]["multiple_ligands"]
                self.assertEqual(feature["status"], expected_status)
                self.assertEqual(feature["minimum_version"], "1.2.0")
                self.assertEqual(
                    feature["supported"],
                    expected_status == "supported",
                )

    def test_vina_autobox_requires_version_1_2_3_or_newer(self) -> None:
        version_completed = SimpleNamespace(
            returncode=0,
            stdout="AutoDock Vina v1.2.2\n",
            stderr="",
        )
        help_completed = SimpleNamespace(
            returncode=0,
            stdout=(
                "  --ligand arg  ligand (PDBQT)\n"
                "  --maps arg  load precomputed affinity maps\n"
                "  --write_maps arg  write precomputed affinity maps\n"
                "  --autobox  derive the grid from the input ligand\n"
                "  --force_even_voxels  use even voxel counts\n"
            ),
            stderr="",
        )

        with patch.object(
            vina_adapter.subprocess,
            "run",
            side_effect=[version_completed, help_completed],
        ):
            result = vina_adapter._run_version_check(
                "C:/tools/vina.exe",
                "configured",
            )

        autobox = result.capabilities["features"]["autobox"]
        self.assertEqual(autobox["status"], "unsupported")
        self.assertFalse(autobox["supported"])
        self.assertTrue(autobox["advertised"])
        self.assertEqual(autobox["minimum_version"], "1.2.3")
        self.assertFalse(autobox["version_compatible"])

    def test_vina_semver_comparison_treats_1_2_10_as_newer_than_1_2_4(self) -> None:
        version_completed = SimpleNamespace(
            returncode=0,
            stdout="wrapper notice\nAutoDock Vina v1.2.10\n",
            stderr="",
        )
        help_completed = SimpleNamespace(
            returncode=0,
            stdout=(
                "  --ligand arg  ligand (PDBQT)\n"
                "  --maps arg  load precomputed affinity maps\n"
                "  --write_maps arg  write precomputed affinity maps\n"
                "  --autobox  derive the grid from the input ligand\n"
                "  --no_refine       use precalculated grids\n"
                "  --force_even_voxels  use even voxel counts\n"
                "  --unbound_energy arg (=nan)  set unbound energy for score-only jobs\n"
            ),
            stderr="",
        )

        with patch.object(
            vina_adapter.subprocess,
            "run",
            side_effect=[version_completed, help_completed],
        ):
            result = vina_adapter._run_version_check("C:/tools/vina.exe", "configured")

        self.assertEqual(result.version, "1.2.10")
        self.assertEqual(result.capabilities["status"], "ok")
        self.assertTrue(
            result.capabilities["features"]["no_refine"]["version_compatible"],
        )
        self.assertTrue(result.capabilities["features"]["no_refine"]["supported"])
        self.assertTrue(result.capabilities["features"]["unbound_energy"]["supported"])

    def test_vina_prerelease_same_core_fails_closed(self) -> None:
        version_completed = SimpleNamespace(
            returncode=0,
            stdout="AutoDock Vina v1.2.4-rc1\n",
            stderr="",
        )
        help_completed = SimpleNamespace(
            returncode=0,
            stdout=(
                "  --autobox  derive the grid from the input ligand\n"
                "  --no_refine       use precalculated grids\n"
                "  --force_even_voxels  use even voxel counts\n"
                "  --unbound_energy arg (=nan)  set unbound energy for score-only jobs\n"
            ),
            stderr="",
        )

        with patch.object(
            vina_adapter.subprocess,
            "run",
            side_effect=[version_completed, help_completed],
        ):
            result = vina_adapter._run_version_check("C:/tools/vina.exe", "configured")

        features = result.capabilities["features"]
        self.assertEqual(result.version, "1.2.4-rc1")
        self.assertEqual(result.capabilities["status"], "partial")
        self.assertEqual(features["no_refine"]["status"], "unsupported")
        self.assertFalse(features["no_refine"]["version_compatible"])
        self.assertEqual(features["unbound_energy"]["status"], "unsupported")
        self.assertFalse(features["unbound_energy"]["version_compatible"])
        self.assertEqual(features["force_even_voxels"]["status"], "supported")
        self.assertIn("1.2.4-rc1", features["unbound_energy"]["message"])
        self.assertIn("1.2.4", features["unbound_energy"]["message"])

    def test_vina_semver_comparison_accepts_stable_and_build_metadata(self) -> None:
        self.assertTrue(vina_adapter._version_at_least("1.2.4", "1.2.4"))
        self.assertTrue(vina_adapter._version_at_least("1.2.4+build.7", "1.2.4"))
        self.assertTrue(vina_adapter._version_at_least("1.2.10", "1.2.4"))
        self.assertFalse(vina_adapter._version_at_least("1.2.4-rc1", "1.2.4"))

    def test_vina_help_failure_keeps_base_detection_available(self) -> None:
        version_completed = SimpleNamespace(
            returncode=0,
            stdout="AutoDock Vina v1.2.7\n",
            stderr="",
        )
        help_completed = SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="unknown option --help_advanced",
        )

        with patch.object(
            vina_adapter.subprocess,
            "run",
            side_effect=[version_completed, help_completed],
        ) as run_mock:
            result = vina_adapter._run_version_check("C:/tools/vina.exe", "configured")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.version, "1.2.7")
        self.assertEqual(result.capabilities["status"], "unknown")
        self.assertTrue(result.capabilities["checked"])
        self.assertEqual(result.capabilities["help_exit_code"], 2)
        self.assertEqual(
            result.capabilities["features"]["no_refine"]["status"],
            "unknown",
        )
        self.assertEqual(
            result.capabilities["features"]["unbound_energy"]["status"],
            "unknown",
        )
        self.assertEqual(
            result.capabilities["features"]["unbound_energy"]["minimum_version"],
            "1.2.4",
        )
        self.assertEqual(
            run_mock.call_args_list[1].args[0],
            ["C:/tools/vina.exe", "--help_advanced"],
        )

    def test_vina_help_timeout_reports_unknown_capabilities(self) -> None:
        version_completed = SimpleNamespace(
            returncode=0,
            stdout="AutoDock Vina v1.2.7\n",
            stderr="",
        )
        timeout = subprocess.TimeoutExpired(
            cmd=["C:/tools/vina.exe", "--help_advanced"],
            timeout=10,
            output="partial help",
            stderr="probe timed out",
        )

        with patch.object(
            vina_adapter.subprocess,
            "run",
            side_effect=[version_completed, timeout],
        ):
            result = vina_adapter._run_version_check("C:/tools/vina.exe", "configured")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.capabilities["status"], "unknown")
        self.assertIsNone(result.capabilities["help_exit_code"])
        self.assertEqual(
            result.capabilities["features"]["force_even_voxels"]["status"],
            "unknown",
        )
        self.assertEqual(
            result.capabilities["features"]["unbound_energy"]["status"],
            "unknown",
        )

    def test_toolchain_paths_use_dev_project_resources_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(os.environ, {TOOLCHAIN_ROOT_ENV_VAR: str(root)}, clear=False):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)

                self.assertEqual(get_runtime_mode(), "dev")
                self.assertEqual(get_toolchain_root(), root / "resources")
                self.assertEqual(get_bundled_vina_path(), root / "resources" / "vina" / "vina.exe")
                self.assertEqual(get_legacy_bundled_vina_path(), root / "resources" / "tools" / "vina" / "vina.exe")
                self.assertEqual(get_bundled_python_path(), root / "resources" / "python" / "python.exe")
                self.assertEqual(get_licenses_dir(), root / "resources" / "licenses")
                self.assertEqual(get_toolchain_manifest_path(), root / "resources" / "toolchain_manifest.json")

    def test_toolchain_paths_use_packaged_resource_dir_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resource_dir = Path(temp_dir) / "tauri_resource_dir"
            project_root = Path(temp_dir) / "repo"
            with patch.dict(
                os.environ,
                {
                    RESOURCE_DIR_ENV_VAR: str(resource_dir),
                    TOOLCHAIN_ROOT_ENV_VAR: str(project_root),
                },
            ):
                self.assertEqual(get_runtime_mode(), "packaged")
                self.assertEqual(get_toolchain_root(), resource_dir / "resources")
                self.assertEqual(get_bundled_vina_path(), resource_dir / "resources" / "vina" / "vina.exe")
                self.assertEqual(
                    get_legacy_bundled_vina_path(),
                    resource_dir / "resources" / "tools" / "vina" / "vina.exe",
                )
                self.assertEqual(get_bundled_python_path(), resource_dir / "resources" / "python" / "python.exe")

    def test_toolchain_status_returns_structured_result(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")
        autogrid_result = ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="missing",
            message="仅 AutoDock4 maps 协议需要。",
            source="missing",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_path = root / "resources" / "vina" / "vina.exe"
            notices_path = root / "resources" / "licenses" / "THIRD_PARTY_NOTICES.md"
            license_path = root / "resources" / "licenses" / "AutoDock-Vina_LICENSE.txt"
            manifest_path = root / "resources" / "toolchain_manifest.json"
            bundled_path.parent.mkdir(parents=True)
            notices_path.parent.mkdir(parents=True)
            bundled_path.write_text("fake bundled vina", encoding="utf-8")
            notices_path.write_text("# Notices\n", encoding="utf-8")
            license_path.write_text("Apache License 2.0\n", encoding="utf-8")
            manifest_path.write_text('{"schema_version": 1}\n', encoding="utf-8")
            settings_path = root / "settings.json"

            with (
                patch.dict(
                    os.environ,
                    {
                        TOOLCHAIN_ROOT_ENV_VAR: str(root),
                        SETTINGS_ENV_VAR: str(settings_path),
                    },
                ),
                patch.object(vina_adapter.subprocess, "run", return_value=completed),
                patch("dockstart_core.toolchain.autogrid_adapter.detect", return_value=autogrid_result) as autogrid_detect,
            ):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
                response = get_toolchain_status()

        self.assertTrue(response["ok"])
        self.assertEqual(response["full_status"], "ready")
        self.assertEqual(response["runtime_mode"], "dev")
        self.assertEqual(response["toolchain_root"], str(root / "resources"))
        self.assertTrue(response["bundled_vina"]["exists"])
        self.assertEqual(response["bundled_vina"]["version"], "1.2.5")
        self.assertEqual(response["active_source"], "bundled")
        self.assertEqual(response["autogrid4"]["status"], "missing")
        self.assertEqual(response["autogrid4_source"], "missing")
        autogrid_detect.assert_called_once_with("")

    def test_toolchain_status_uses_configured_autogrid_without_affecting_vina(self) -> None:
        vina_result = ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version="1.2.7",
            path="C:/tools/vina.exe",
            message="Vina 可用。",
            source="configured",
        )
        autogrid_result = ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="ok",
            version="4.2.7",
            path="C:/tools/autogrid4.exe",
            message="AutoGrid4 可用。",
            source="configured",
        )
        python_result = ToolCheckResult(
            key="python",
            name="Python",
            status="ok",
            version="3.11",
            path=sys.executable,
            message="Python 可用。",
            source="configured",
        )
        missing_python_tool = ToolCheckResult(
            key="python-package",
            name="Python package",
            status="missing",
            message="未检测到。",
            source="configured",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings_path = root / "settings.json"
            save_payload = DockStartSettings(
                tool_paths=ToolPaths(
                    vina="C:/tools/vina.exe",
                    python=sys.executable,
                    autogrid4="C:/tools/autogrid4.exe",
                ),
            )
            with patch.dict(
                os.environ,
                {
                    TOOLCHAIN_ROOT_ENV_VAR: str(root),
                    SETTINGS_ENV_VAR: str(settings_path),
                },
                clear=False,
            ):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
                save_settings(save_payload)
                with (
                    patch("dockstart_core.toolchain.vina_adapter.detect", return_value=vina_result),
                    patch("dockstart_core.toolchain.autogrid_adapter.detect", return_value=autogrid_result) as autogrid_detect,
                    patch("dockstart_core.toolchain.get_resolved_python", return_value=python_result),
                    patch("dockstart_core.toolchain.rdkit_adapter.detect", return_value=missing_python_tool),
                    patch("dockstart_core.toolchain.meeko_adapter.detect", return_value=missing_python_tool),
                ):
                    response = get_toolchain_status()

        self.assertTrue(response["ok"])
        self.assertEqual(response["active_vina"]["status"], "ok")
        self.assertEqual(response["autogrid4"]["status"], "ok")
        self.assertEqual(response["autogrid4"]["version"], "4.2.7")
        autogrid_detect.assert_called_once_with("C:/tools/autogrid4.exe")

    def test_missing_autogrid_does_not_change_first_run_guidance(self) -> None:
        ok_tool = ToolCheckResult(
            key="tool",
            name="Tool",
            status="ok",
            version="1.0",
            path=sys.executable,
            message="可用。",
            source="configured",
        )
        missing_autogrid = ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="missing",
            message="仅 AutoDock4 maps 协议需要。",
            source="missing",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(
                os.environ,
                {
                    TOOLCHAIN_ROOT_ENV_VAR: str(root),
                    SETTINGS_ENV_VAR: str(root / "settings.json"),
                },
                clear=False,
            ):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
                with (
                    patch("dockstart_core.toolchain.vina_adapter.detect", return_value=ok_tool),
                    patch("dockstart_core.toolchain.autogrid_adapter.detect", return_value=missing_autogrid),
                    patch("dockstart_core.toolchain.get_resolved_python", return_value=ok_tool),
                    patch("dockstart_core.toolchain.rdkit_adapter.detect", return_value=ok_tool),
                    patch("dockstart_core.toolchain.meeko_adapter.detect", return_value=ok_tool),
                ):
                    response = get_toolchain_status()

        self.assertEqual(response["autogrid4"]["status"], "missing")
        self.assertEqual(response["first_run_guidance"]["status"], "ready")

    def test_toolchain_status_uses_packaged_resource_dir(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            resource_dir = Path(temp_dir) / "tauri_resource_dir"
            toolchain_root = resource_dir / "resources"
            bundled_path = toolchain_root / "vina" / "vina.exe"
            notices_path = toolchain_root / "licenses" / "THIRD_PARTY_NOTICES.md"
            license_path = toolchain_root / "licenses" / "AutoDock-Vina_LICENSE.txt"
            manifest_path = toolchain_root / "toolchain_manifest.json"
            bundled_path.parent.mkdir(parents=True)
            notices_path.parent.mkdir(parents=True)
            bundled_path.write_text("fake bundled vina", encoding="utf-8")
            notices_path.write_text("# Notices\n", encoding="utf-8")
            license_path.write_text("Apache License 2.0\n", encoding="utf-8")
            manifest_path.write_text('{"schema_version": 1}\n', encoding="utf-8")
            settings_path = Path(temp_dir) / "settings.json"

            with (
                patch.dict(
                    os.environ,
                    {
                        RESOURCE_DIR_ENV_VAR: str(resource_dir),
                        SETTINGS_ENV_VAR: str(settings_path),
                    },
                ),
                patch.object(vina_adapter.subprocess, "run", return_value=completed),
            ):
                response = get_toolchain_status()

        self.assertTrue(response["ok"])
        self.assertEqual(response["runtime_mode"], "packaged")
        self.assertEqual(response["resource_dir"], str(resource_dir.resolve()))
        self.assertEqual(response["toolchain_root"], str(toolchain_root.resolve()))
        self.assertEqual(response["bundled_vina"]["path"], str(bundled_path.resolve()))
        self.assertEqual(response["manifest_file"], str(manifest_path.resolve()))
        self.assertEqual(response["licenses_dir"], str((toolchain_root / "licenses").resolve()))
        self.assertEqual(response["active_source"], "bundled")

    def test_toolchain_status_missing_when_packaged_resource_dir_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resource_dir = Path(temp_dir) / "missing_tauri_resource_dir"
            settings_path = Path(temp_dir) / "settings.json"

            with (
                patch.dict(
                    os.environ,
                    {
                        RESOURCE_DIR_ENV_VAR: str(resource_dir),
                        SETTINGS_ENV_VAR: str(settings_path),
                    },
                ),
                patch.object(vina_adapter.shutil, "which", return_value=None),
            ):
                response = get_toolchain_status()

        self.assertTrue(response["ok"])
        self.assertEqual(response["runtime_mode"], "packaged")
        self.assertEqual(response["full_status"], "missing")
        self.assertFalse(response["resources"]["exists"])
        self.assertEqual(response["bundled_vina"]["status"], "missing")
        self.assertEqual(response["active_source"], "missing")

    def test_meeko_import_failure_returns_structured_result(self) -> None:
        completed = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'meeko'",
        )

        with patch.object(meeko_adapter.subprocess, "run", return_value=completed):
            result = meeko_adapter.detect(sys.executable, "configured")

        self.assertEqual(result.key, "meeko")
        self.assertEqual(result.status, "missing")
        self.assertIn("Meeko", result.name)
        self.assertEqual(result.source, "configured")

    def test_rdkit_import_failure_returns_structured_result(self) -> None:
        completed = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'rdkit'",
        )

        with patch.object(rdkit_adapter.subprocess, "run", return_value=completed):
            result = rdkit_adapter.detect(sys.executable, "configured")

        self.assertEqual(result.key, "rdkit")
        self.assertEqual(result.status, "missing")
        self.assertIn("RDKit", result.name)
        self.assertEqual(result.source, "configured")

    def test_meeko_and_rdkit_with_missing_configured_python_do_not_raise(self) -> None:
        missing_python = "Z:/missing/python.exe"

        meeko_result = meeko_adapter.detect(missing_python, "configured")
        rdkit_result = rdkit_adapter.detect(missing_python, "configured")

        self.assertEqual(meeko_result.status, "missing")
        self.assertEqual(rdkit_result.status, "missing")
        self.assertEqual(meeko_result.source, "configured")
        self.assertEqual(rdkit_result.source, "configured")


if __name__ == "__main__":
    unittest.main()
