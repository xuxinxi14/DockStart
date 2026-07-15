from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_installed_basic_release.py"
SPEC = importlib.util.spec_from_file_location("verify_installed_basic_release", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class InstalledBasicReleaseGateTests(unittest.TestCase):
    def _temporary_repo(self, root: Path) -> Path:
        repo = root / "DockStart source"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "verify_basic_release.py").write_text("# gate fixture\n", encoding="utf-8")
        tauri = repo / "apps" / "desktop" / "src-tauri"
        tauri.mkdir(parents=True)
        (tauri / "tauri.conf.json").write_text(
            '{"bundle":{"publisher":"XinXi Xu"}}\n',
            encoding="utf-8",
        )
        return repo

    def _create_installed_layout(self, paths: object) -> None:
        required = (
            paths.install_root / "dockstart-desktop.exe",
            paths.install_root / "uninstall.exe",
            paths.install_root / "backend" / "dockstart_core" / "project.py",
            paths.install_root / "resources" / "python" / "python.exe",
            paths.install_root / "resources" / "vina" / "vina.exe",
        )
        for path in required:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        manifest = paths.install_root / "resources" / "toolchain_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "release_profile": "basic_stable",
                    "includes_bundled_rdkit": False,
                    "includes_bundled_meeko": False,
                },
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _verification_payload() -> dict[str, object]:
        return {
            "ok": True,
            "profile": "basic_stable",
            "run_status": "finished",
            "run_id": "run_001",
            "exit_code": 0,
            "best_affinity": -0.7,
            "pose_count": 9,
            "repeat_run_id": "run_002",
            "repeat_run_status": "finished",
            "basic_mode_available": True,
            "assisted_mode_available": False,
        }

    @staticmethod
    def _clean_uninstall() -> dict[str, object]:
        return {
            "exit_code": 0,
            "clean": True,
            "cleanup_error": "",
            "install_directory_removed": True,
            "runtime_residue_detected": False,
            "uninstall_record_residue": False,
            "manufacturer_key_removed": True,
            "forced_cleanup_after_failure": False,
            "residue_count": 0,
        }

    def test_install_root_is_fixed_below_basic_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            paths = MODULE._resolve_gate_paths(repo)
            self.assertEqual(paths.install_root, repo / ".release" / "basic-install-gate" / "installed")
            self.assertEqual(
                paths.result_json,
                repo / ".release" / "basic-install-gate" / "post-install-gate.json",
            )
            MODULE._assert_path_within_gate(paths.install_root, paths.gate_root)
            with self.assertRaises(MODULE.InstalledBasicGateError):
                MODULE._assert_path_within_gate(repo / "outside", paths.gate_root)

    def test_nonempty_install_root_is_rejected_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            paths = MODULE._resolve_gate_paths(repo)
            paths.install_root.mkdir(parents=True)
            sentinel = paths.install_root / "keep-me.txt"
            sentinel.write_text("do not delete", encoding="utf-8")
            with self.assertRaises(MODULE.InstalledBasicGateError):
                MODULE._assert_install_root_empty(paths)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not delete")

    def test_nsis_arguments_keep_guarded_path_last(self) -> None:
        installer = Path(r"C:\artifacts\DockStart Basic setup.exe")
        install_root = Path(r"C:\source tree\.release\basic-install-gate\installed")
        install_command = MODULE._nsis_install_command(installer, install_root)
        self.assertEqual(install_command[1:3], ["/S", "/NS"])
        self.assertEqual(install_command[-1], f"/D={install_root}")

        uninstall_command = MODULE._nsis_uninstall_command(Path(r"C:\gate\uninstall-gate.exe"), install_root)
        self.assertEqual(uninstall_command[1], "/S")
        self.assertEqual(uninstall_command[-1], f"_?={install_root}")

    def test_installed_layout_requires_basic_profile_and_matching_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            paths = MODULE._resolve_gate_paths(repo)
            self._create_installed_layout(paths)
            record = {"install_location": str(paths.install_root)}
            with (
                patch.object(MODULE, "_registry_records", return_value=[record]),
                patch.object(MODULE, "_manufacturer_location", return_value=str(paths.install_root)),
            ):
                MODULE._assert_installed_layout(paths)

            with (
                patch.object(MODULE, "_registry_records", return_value=[]),
                patch.object(MODULE, "_manufacturer_location", return_value=""),
                self.assertRaises(MODULE.InstalledBasicGateError),
            ):
                MODULE._assert_installed_layout(paths)

    def test_post_install_verifier_invokes_basic_two_run_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            paths = MODULE._resolve_gate_paths(repo)
            paths.diagnostics_root.mkdir(parents=True)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(self._verification_payload()),
                stderr="",
            )
            with (
                patch.object(MODULE, "_run_logged", return_value=completed) as run_logged,
                patch.object(MODULE, "_generated_bytecode_entries", return_value=[]),
            ):
                result = MODULE._run_post_install_verifier(paths)
            command = run_logged.call_args.args[0]
            self.assertIn(str(repo / "scripts" / "verify_basic_release.py"), command)
            self.assertIn(str(paths.install_root), command)
            self.assertIn("--keep-work-dir", command)
            self.assertNotIn("--gate", command)
            self.assertEqual(result["run_status"], "finished")
            self.assertEqual(result["repeat_run_status"], "finished")

    def test_gate_writes_passed_json_only_after_clean_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            installer = repo / "DockStart_0.10.2_Basic_x64-setup.exe"
            installer.write_bytes(b"installer")
            install_completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            with (
                patch.object(MODULE, "_assert_no_existing_installation"),
                patch.object(MODULE, "_run_logged", return_value=install_completed),
                patch.object(MODULE, "_assert_installed_layout"),
                patch.object(MODULE, "_run_post_install_verifier", return_value=self._verification_payload()),
                patch.object(MODULE, "_cleanup_installed_layout", return_value=self._clean_uninstall()),
            ):
                result = MODULE.run_installed_gate(repo, installer)

            paths = MODULE._resolve_gate_paths(repo)
            recorded = json.loads(paths.result_json.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "passed")
            self.assertEqual(recorded["status"], "passed")
            self.assertEqual(recorded["profile"], "basic_stable")
            self.assertEqual(recorded["verification"]["repeat_run_status"], "finished")
            self.assertTrue(recorded["uninstall"]["clean"])

    def test_gate_fails_when_uninstall_leaves_residue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            installer = repo / "DockStart_0.10.2_Basic_x64-setup.exe"
            installer.write_bytes(b"installer")
            install_completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            dirty = self._clean_uninstall()
            dirty.update(
                {
                    "clean": False,
                    "install_directory_removed": False,
                    "runtime_residue_detected": True,
                    "residue_count": 1,
                },
            )
            with (
                patch.object(MODULE, "_assert_no_existing_installation"),
                patch.object(MODULE, "_run_logged", return_value=install_completed),
                patch.object(MODULE, "_assert_installed_layout"),
                patch.object(MODULE, "_run_post_install_verifier", return_value=self._verification_payload()),
                patch.object(MODULE, "_cleanup_installed_layout", return_value=dirty),
                self.assertRaises(MODULE.InstalledBasicGateError),
            ):
                MODULE.run_installed_gate(repo, installer)

            recorded = json.loads(
                (repo / ".release" / "basic-install-gate" / "post-install-gate.json").read_text(
                    encoding="utf-8",
                ),
            )
            self.assertEqual(recorded["status"], "failed")
            self.assertFalse(recorded["uninstall"]["clean"])
            self.assertIn("Silent uninstall left", recorded["error"])

    def test_install_failure_remains_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            installer = repo / "DockStart_0.10.2_Basic_x64-setup.exe"
            installer.write_bytes(b"installer")
            install_completed = subprocess.CompletedProcess(args=[], returncode=7, stdout="", stderr="failed")
            with (
                patch.object(MODULE, "_assert_no_existing_installation"),
                patch.object(MODULE, "_run_logged", return_value=install_completed),
                self.assertRaises(MODULE.InstalledBasicGateError),
            ):
                MODULE.run_installed_gate(repo, installer)

            recorded = json.loads(
                (repo / ".release" / "basic-install-gate" / "post-install-gate.json").read_text(
                    encoding="utf-8",
                ),
            )
            self.assertEqual(recorded["status"], "failed")
            self.assertIsNone(recorded["verification"])
            self.assertEqual(recorded["uninstall"]["clean"], True)
            self.assertIn("exit code 7", recorded["error"])


if __name__ == "__main__":
    unittest.main()
