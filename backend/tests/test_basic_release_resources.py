import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "prepare_basic_release_resources.py"
SPEC = importlib.util.spec_from_file_location("prepare_basic_release_resources", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BasicReleaseResourceTests(unittest.TestCase):
    def test_staging_excludes_assisted_packages_and_cli_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "backend" / "adapters").mkdir(parents=True)
            (root / "backend" / "adapters" / "__init__.py").write_text("", encoding="utf-8")
            (root / "backend" / "dockstart_core").mkdir(parents=True)
            (root / "backend" / "dockstart_core" / "__init__.py").write_text("", encoding="utf-8")
            (root / "backend" / "dockstart_core" / "__pycache__").mkdir()
            (root / "backend" / "dockstart_core" / "__pycache__" / "stale.pyc").write_bytes(b"stale")
            (root / "apps" / "desktop").mkdir(parents=True)
            (root / "apps" / "desktop" / "package.json").write_text(
                '{"name":"dockstart-desktop","version":"0.10.0"}',
                encoding="utf-8",
            )
            (root / "LICENSE").write_text("DockStart Apache-2.0 license", encoding="utf-8")
            resources = root / "resources"
            (resources / "vina").mkdir(parents=True)
            (resources / "vina" / "vina.exe").write_bytes(b"vina")
            (resources / "licenses").mkdir()
            (resources / "licenses" / "AutoDock-Vina_LICENSE.txt").write_text("license", encoding="utf-8")
            (resources / "licenses" / "Python_LICENSE.txt").write_text("license", encoding="utf-8")
            (resources / "licenses" / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")
            for license_name in (
                "3Dmol_LICENSE.txt",
                "React_LICENSE.txt",
                "React-DOM_LICENSE.txt",
                "Phosphor-Icons_LICENSE.txt",
                "Tauri_LICENSE_APACHE-2.0.txt",
                "Tauri_LICENSE_MIT.txt",
                "Tauri-plugin-dialog_LICENSE.spdx",
                "Serde_LICENSE-MIT.txt",
            ):
                (resources / "licenses" / license_name).write_text("license", encoding="utf-8")
            (resources / "examples" / "basic_pdbqt").mkdir(parents=True)
            (resources / "examples" / "basic_pdbqt" / "project.json").write_text("{}", encoding="utf-8")
            (resources / "examples" / "basic_pdbqt" / "manifest.json").write_text("{}", encoding="utf-8")
            (resources / "examples" / "basic_pdbqt" / "receptor.pdbqt").write_text("ATOM", encoding="utf-8")
            (resources / "examples" / "basic_pdbqt" / "ligand.pdbqt").write_text("ATOM", encoding="utf-8")
            runtime = resources / "python"
            (runtime / "DLLs").mkdir(parents=True)
            (runtime / "Lib" / "site-packages" / "meeko").mkdir(parents=True)
            (runtime / "Lib" / "site-packages" / "rdkit").mkdir(parents=True)
            (runtime / "Scripts").mkdir()
            (runtime / "python.exe").write_bytes(b"python")
            (runtime / "python311.dll").write_bytes(b"dll")
            (runtime / "Lib" / "json.py").write_text("# stdlib", encoding="utf-8")
            (runtime / "Lib" / "ensurepip" / "_bundled").mkdir(parents=True)
            (runtime / "Lib" / "ensurepip" / "_bundled" / "pip-test.whl").write_bytes(b"pip")
            (runtime / "Lib" / "site-packages" / "meeko" / "__init__.py").write_text("", encoding="utf-8")
            (runtime / "Scripts" / "mk_prepare_ligand.py").write_text("", encoding="utf-8")
            (runtime / "README.md").write_text("runtime", encoding="utf-8")
            (resources / "toolchain_manifest.json").write_text(
                json.dumps(
                    {
                        "bundled_vina": {
                            "version": "1.2.7",
                            "source": "test Vina",
                            "sha256": hashlib.sha256(b"vina").hexdigest(),
                        },
                        "bundled_python": {
                            "version": "3.11.15",
                            "source": "test Python",
                            "sha256": hashlib.sha256(b"python").hexdigest(),
                        },
                    },
                ),
                encoding="utf-8",
            )

            result = MODULE.prepare_basic_release_resources(
                root,
                validate_runtime=False,
                generate_dependency_licenses=False,
                prepared_at="2026-07-13T00:00:00+00:00",
            )
            stage = root / ".release" / "basic" / "resources"
            self.assertTrue(result["ok"])
            self.assertTrue((stage / "python" / "python.exe").is_file())
            self.assertTrue((stage / "python" / "Lib" / "json.py").is_file())
            self.assertFalse((stage / "python" / "Lib" / "site-packages").exists())
            self.assertFalse((stage / "python" / "Scripts").exists())
            self.assertFalse((stage / "python" / "Lib" / "ensurepip").exists())
            self.assertTrue((root / ".release" / "basic" / "backend" / "dockstart_core").is_dir())
            self.assertFalse(
                (root / ".release" / "basic" / "backend" / "dockstart_core" / "__pycache__").exists(),
            )
            self.assertTrue((root / ".release" / "basic" / "frontend" / "package.json").is_file())
            self.assertTrue((stage / "licenses" / "DockStart-Apache-2.0.txt").is_file())
            manifest = json.loads((stage / "toolchain_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["release_profile"], "basic_stable")
            self.assertFalse(manifest["includes_bundled_rdkit"])
            self.assertFalse(manifest["includes_bundled_meeko"])
            self.assertEqual(manifest["bundled_python"]["role"], "backend_runtime")
            self.assertNotIn("packages", manifest["bundled_python"])

    def test_staging_target_must_remain_under_release_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(MODULE.BasicReleasePreparationError):
                MODULE.prepare_basic_release_resources(
                    root,
                    root / "outside",
                    validate_runtime=False,
                )


if __name__ == "__main__":
    unittest.main()
