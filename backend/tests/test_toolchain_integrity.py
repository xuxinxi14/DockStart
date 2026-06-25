from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
for import_path in (BACKEND_ROOT, SCRIPTS_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from dockstart_core.settings import SETTINGS_ENV_VAR  # noqa: E402
from dockstart_core.toolchain import (  # noqa: E402
    calculate_file_sha256,
    get_bundled_vina_integrity,
    validate_bundled_vina_package,
)
from dockstart_core.toolchain_paths import RESOURCE_DIR_ENV_VAR, TOOLCHAIN_ROOT_ENV_VAR  # noqa: E402
from prepare_bundled_vina import prepare_bundled_vina  # noqa: E402


class ToolchainIntegrityTests(unittest.TestCase):
    def _write_manifest(self, repo_root: Path, sha256: str, bundled: bool = True) -> None:
        manifest_path = repo_root / "resources" / "toolchain_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "bundled_vina": {
                        "name": "AutoDock Vina",
                        "version": "1.2.7",
                        "binary_path": "resources/tools/vina/vina.exe",
                        "license": "Apache-2.0",
                        "source": "unit-test",
                        "bundled": bundled,
                        "sha256": sha256,
                        "prepared_at": "2099-01-01T00:00:00+00:00",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_notice(self, repo_root: Path) -> None:
        notices_path = repo_root / "resources" / "licenses" / "THIRD_PARTY_NOTICES.md"
        notices_path.parent.mkdir(parents=True, exist_ok=True)
        notices_path.write_text("# Notices\n\n## AutoDock Vina\n\nLicense: Apache-2.0\n", encoding="utf-8")

    def test_calculate_file_sha256_returns_expected_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "sample.bin"
            target.write_bytes(b"abc")

            self.assertEqual(
                calculate_file_sha256(target),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )

    def test_prepare_bundled_vina_copies_fake_binary_dll_and_updates_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            source_dir = Path(temp_dir) / "vina_release"
            source_dir.mkdir(parents=True)
            (source_dir / "vina.exe").write_bytes(b"fake vina binary")
            (source_dir / "support.dll").write_bytes(b"fake dll")
            (source_dir / "LICENSE.txt").write_text("Apache License 2.0\n", encoding="utf-8")

            response = prepare_bundled_vina(source_dir, repo_root=root, version="1.2.7", source_label="unit-test")

            target_binary = root / "resources" / "tools" / "vina" / "vina.exe"
            target_dll = root / "resources" / "tools" / "vina" / "support.dll"
            target_license = root / "resources" / "licenses" / "AutoDock-Vina_LICENSE.txt"
            manifest = json.loads((root / "resources" / "toolchain_manifest.json").read_text(encoding="utf-8"))

            self.assertTrue(response["ok"])
            self.assertTrue(target_binary.is_file())
            self.assertTrue(target_dll.is_file())
            self.assertTrue(target_license.is_file())
            self.assertEqual(response["sha256"], calculate_file_sha256(target_binary))
            self.assertEqual(manifest["bundled_vina"]["sha256"], response["sha256"])
            self.assertEqual(manifest["bundled_vina"]["version"], "1.2.7")
            self.assertTrue(manifest["bundled_vina"]["bundled"])

    def test_missing_license_returns_incomplete_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            vina_path = root / "resources" / "tools" / "vina" / "vina.exe"
            vina_path.parent.mkdir(parents=True)
            vina_path.write_bytes(b"fake vina binary")
            sha256 = calculate_file_sha256(vina_path)
            self._write_manifest(root, sha256)
            self._write_notice(root)
            settings_path = Path(temp_dir) / "settings.json"

            with patch.dict(os.environ, {TOOLCHAIN_ROOT_ENV_VAR: str(root), SETTINGS_ENV_VAR: str(settings_path)}):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
                integrity = get_bundled_vina_integrity()

            self.assertEqual(integrity["status"], "incomplete")
            self.assertTrue(any("AutoDock-Vina_LICENSE.txt" in warning for warning in integrity["warnings"]))
            self.assertFalse(integrity["license_exists"])

    def test_license_and_notice_ready_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            vina_path = root / "resources" / "tools" / "vina" / "vina.exe"
            license_path = root / "resources" / "licenses" / "AutoDock-Vina_LICENSE.txt"
            vina_path.parent.mkdir(parents=True)
            license_path.parent.mkdir(parents=True)
            vina_path.write_bytes(b"fake vina binary")
            license_path.write_text("Apache License 2.0\n", encoding="utf-8")
            sha256 = calculate_file_sha256(vina_path)
            self._write_manifest(root, sha256)
            self._write_notice(root)
            settings_path = Path(temp_dir) / "settings.json"

            with patch.dict(os.environ, {TOOLCHAIN_ROOT_ENV_VAR: str(root), SETTINGS_ENV_VAR: str(settings_path)}):
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
                response = validate_bundled_vina_package()

            self.assertTrue(response["ok"])
            self.assertEqual(response["status"], "ready")
            self.assertEqual(response["warnings"], [])


if __name__ == "__main__":
    unittest.main()
