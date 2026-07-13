from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_installed_assisted_release.py"
SPEC = importlib.util.spec_from_file_location("verify_installed_assisted_release", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class InstalledAssistedReleaseGateTests(unittest.TestCase):
    def _temporary_repo(self, root: Path) -> Path:
        repo = root / "DockStart source"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "verify_assisted_release.py").write_text("# gate fixture\n", encoding="utf-8")
        return repo

    def test_install_root_is_fixed_below_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            paths = MODULE._resolve_gate_paths(repo)
            self.assertEqual(paths.install_root, repo / ".release" / "install-gate" / "installed")
            self.assertEqual(paths.result_json, repo / ".release" / "install-gate" / "post-install-gate.json")
            MODULE._assert_path_within_gate(paths.install_root, paths.gate_root)
            with self.assertRaises(MODULE.InstalledAssistedGateError):
                MODULE._assert_path_within_gate(repo / "outside", paths.gate_root)

    def test_nonempty_install_root_is_rejected_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            paths = MODULE._resolve_gate_paths(repo)
            paths.install_root.mkdir(parents=True)
            sentinel = paths.install_root / "keep-me.txt"
            sentinel.write_text("do not delete", encoding="utf-8")
            with self.assertRaises(MODULE.InstalledAssistedGateError):
                MODULE._assert_install_root_empty(paths)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not delete")

    def test_nsis_arguments_keep_guarded_path_last(self) -> None:
        installer = Path(r"C:\artifacts\DockStart setup.exe")
        install_root = Path(r"C:\source tree\.release\install-gate\installed")
        install_command = MODULE._nsis_install_command(installer, install_root)
        self.assertEqual(install_command[1:3], ["/S", "/NS"])
        self.assertEqual(install_command[-1], f"/D={install_root}")

        uninstall_command = MODULE._nsis_uninstall_command(Path(r"C:\gate\uninstall-gate.exe"), install_root)
        self.assertEqual(uninstall_command[1], "/S")
        self.assertEqual(uninstall_command[-1], f"_?={install_root}")

    def test_assisted_build_defaults_to_real_install_gate(self) -> None:
        build_script = (REPO_ROOT / "scripts" / "build_windows_assisted_release.ps1").read_text(encoding="utf-8")
        self.assertIn("verify_installed_assisted_release.py", build_script)
        self.assertIn('"cargo test"', build_script)
        self.assertIn('[switch]$SkipPostInstallGate', build_script)
        self.assertIn('$artifactManifest["post_install_gate"] = "pending"', build_script)
        self.assertIn('$artifactManifest["publishable"] = $false', build_script)
        self.assertIn('$artifactManifest["post_install_gate"] = "passed"', build_script)
        self.assertIn('$artifactManifest["publishable"] = $true', build_script)


if __name__ == "__main__":
    unittest.main()
