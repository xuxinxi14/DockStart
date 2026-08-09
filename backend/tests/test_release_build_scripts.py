from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class ReleaseBuildScriptTests(unittest.TestCase):
    def _read_script(self, name: str) -> str:
        return (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")

    def test_basic_build_uses_an_isolated_cargo_target_before_cleanup(self) -> None:
        script = self._read_script("build_windows_release.ps1")

        self.assertIn('.release\\cargo-target\\basic', script)
        self.assertIn('$releaseDir = Join-Path $cargoTargetDir "release"', script)
        self.assertIn('check_release_build_safety.py', script)
        self.assertIn('Assert-CargoTargetDirectory', script)
        self.assertNotIn('$releaseDir = Join-Path $tauriDir "target\\release"', script)
        self.assertLess(
            script.index('Invoke-Checked "Validate release build cleanup safety"'),
            script.index('"Prepare deterministic Basic release stage"'),
        )

    def test_assisted_build_fails_early_for_existing_installs_by_default(self) -> None:
        script = self._read_script("build_windows_assisted_release.ps1")

        self.assertIn('.release\\cargo-target\\assisted', script)
        self.assertIn('$releaseDir = Join-Path $cargoTargetDir "release"', script)
        self.assertIn('check_release_build_safety.py', script)
        self.assertIn('$safetyArguments += "--require-no-existing-install"', script)
        self.assertNotIn('$releaseDir = Join-Path $tauriDir "target\\release"', script)
        self.assertLess(
            script.index('Invoke-Checked "Validate release build and install-gate safety"'),
            script.index('"Prepare deterministic offline Assisted stage"'),
        )

    def test_both_builders_use_the_same_locked_development_gate_and_recheck_provenance(self) -> None:
        for name in ("build_windows_release.ps1", "build_windows_assisted_release.ps1"):
            with self.subTest(name=name):
                script = self._read_script(name)
                self.assertIn('scripts\\check_all.ps1', script)
                self.assertIn('"-RequireReleaseResources"', script)
                self.assertIn('cargo metadata --locked', script)
                self.assertIn('"--", "--locked"', script)
                self.assertIn('Recheck source state before recording provenance', script)
                self.assertIn('branch --show-current', script)
                self.assertIn('rev-parse HEAD', script)
                self.assertIn('$finalSourceCommit -cne $sourceCommit', script)
                self.assertIn('Get-SourceStateFingerprint', script)
                self.assertIn('"source_state_sha256" = $initialSourceStateSha256', script)
                self.assertIn('"tool_versions" = $toolVersions', script)
                self.assertIn('"lockfiles_sha256" = $lockfileSha256', script)
                self.assertIn('CARGO_BUILD_JOBS', script)

    def test_assisted_post_install_gate_uses_and_rehashes_archived_installer(self) -> None:
        script = self._read_script("build_windows_assisted_release.ps1")

        self.assertIn('"--installer", $archivedNsis', script)
        self.assertNotIn('"--installer", $expectedNsis', script)
        self.assertIn('Archived installer changed during the post-install gate', script)
        self.assertGreater(
            script.rindex('Get-SourceStateFingerprint $repoRoot'),
            script.index('verify_installed_assisted_release.py'),
        )

    def test_unified_gate_covers_backend_frontend_rust_and_dependency_audit(self) -> None:
        script = self._read_script("check_all.ps1")

        for expected in (
            '"compileall"',
            '"unittest"',
            '"test:frontend:async"',
            '"audit"',
            '"fmt"',
            '"check"',
            '"test"',
            '"clippy"',
            '"--locked"',
            '"DOCKSTART_REQUIRE_RELEASE_RESOURCES"',
        ):
            self.assertIn(expected, script)


if __name__ == "__main__":
    unittest.main()
