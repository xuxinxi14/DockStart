from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import meeko_adapter, rdkit_adapter, vina_adapter  # noqa: E402
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
from dockstart_core.toolchain_paths import (  # noqa: E402
    RESOURCE_DIR_ENV_VAR,
    TOOLCHAIN_ROOT_ENV_VAR,
    get_bundled_vina_path,
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

    def test_settings_loads_default_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "missing_settings.json"
            with patch.dict(os.environ, {SETTINGS_ENV_VAR: str(settings_path)}):
                settings = load_settings()

        self.assertEqual(settings.tool_paths.vina, "")
        self.assertEqual(settings.tool_paths.python, "")
        self.assertEqual(settings.project.default_project_dir, "")

    def test_settings_saves_and_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "dockstart_settings.json"
            expected = DockStartSettings(
                tool_paths=ToolPaths(vina="C:/tools/vina.exe", python="C:/Python/python.exe"),
            )
            with patch.dict(os.environ, {SETTINGS_ENV_VAR: str(settings_path)}):
                save_settings(expected)
                loaded = load_settings()
                resolved_path = get_settings_path()

        self.assertEqual(loaded.tool_paths.vina, "C:/tools/vina.exe")
        self.assertEqual(loaded.tool_paths.python, "C:/Python/python.exe")
        self.assertEqual(resolved_path, settings_path)

    def test_python_detection_returns_structured_result(self) -> None:
        result = detect_python()

        self.assertEqual(result.key, "python")
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.version)
        self.assertTrue(result.path)
        self.assertEqual(result.source, "current_environment")

    def test_vina_missing_returns_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = str(Path(temp_dir) / "resources" / "tools" / "vina" / "vina.exe")
            with patch.object(vina_adapter.shutil, "which", return_value=None):
                result = vina_adapter.detect(bundled_path=bundled_path)

        self.assertEqual(result.key, "vina")
        self.assertIn(result.status, {"missing", "error"})
        self.assertEqual(result.source, "missing")
        self.assertEqual(result.bundled_path, bundled_path)

    def test_configured_vina_missing_returns_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = str(Path(temp_dir) / "resources" / "tools" / "vina" / "vina.exe")
            result = vina_adapter.detect("Z:/missing/vina.exe", bundled_path=bundled_path)

        self.assertEqual(result.key, "vina")
        self.assertIn(result.status, {"missing", "error"})
        self.assertEqual(result.source, "configured")
        self.assertIn("用户配置", result.message)

    def test_unconfigured_vina_still_uses_auto_detection(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = str(Path(temp_dir) / "resources" / "tools" / "vina" / "vina.exe")
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
            bundled_path = Path(temp_dir) / "resources" / "tools" / "vina" / "vina.exe"
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

    def test_configured_vina_takes_priority_over_path_when_no_bundled(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_path = Path(temp_dir) / "resources" / "tools" / "vina" / "vina.exe"
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
            bundled_path = Path(temp_dir) / "resources" / "tools" / "vina" / "vina.exe"
            configured_path = Path(temp_dir) / "configured" / "vina.exe"
            configured_path.parent.mkdir(parents=True)
            configured_path.write_text("fake configured vina", encoding="utf-8")

            with patch.object(vina_adapter.subprocess, "run", return_value=completed):
                result = vina_adapter.detect(str(configured_path), bundled_path=str(bundled_path))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "configured")
        self.assertFalse(result.is_bundled)

    def test_toolchain_paths_use_dev_project_resources_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(os.environ, {TOOLCHAIN_ROOT_ENV_VAR: str(root)}, clear=False):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)

                self.assertEqual(get_runtime_mode(), "dev")
                self.assertEqual(get_toolchain_root(), root / "resources")
                self.assertEqual(get_bundled_vina_path(), root / "resources" / "tools" / "vina" / "vina.exe")
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
                self.assertEqual(get_bundled_vina_path(), resource_dir / "resources" / "tools" / "vina" / "vina.exe")

    def test_toolchain_status_returns_structured_result(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_path = root / "resources" / "tools" / "vina" / "vina.exe"
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

    def test_toolchain_status_uses_packaged_resource_dir(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="AutoDock Vina v1.2.5\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            resource_dir = Path(temp_dir) / "tauri_resource_dir"
            toolchain_root = resource_dir / "resources"
            bundled_path = toolchain_root / "tools" / "vina" / "vina.exe"
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
