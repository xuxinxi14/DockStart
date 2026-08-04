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


if __name__ == "__main__":
    unittest.main()
