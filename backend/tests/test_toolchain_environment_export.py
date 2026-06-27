from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from export_toolchain_environment import (  # noqa: E402
    build_environment_yml,
    detect_conda_prefix,
    export_toolchain_environment,
)


class ToolchainEnvironmentExportTests(unittest.TestCase):
    def test_non_conda_python_returns_structured_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            python_path = Path(temp_dir) / "python.exe"
            python_path.write_text("fake python", encoding="utf-8")

            response = export_toolchain_environment(
                repo_root=temp_dir,
                python_path=str(python_path),
                dry_run=True,
            )

        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], "not_conda")
        self.assertIn("conda", response["message"])

    def test_detect_conda_prefix_accepts_conda_meta(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_dir = Path(temp_dir) / "envs" / "dockstart-rdkit-meeko"
            env_dir.mkdir(parents=True)
            (env_dir / "conda-meta").mkdir()
            python_path = env_dir / "python.exe"
            python_path.write_text("fake python", encoding="utf-8")

            self.assertEqual(detect_conda_prefix(python_path), env_dir.resolve())

    def test_conda_python_mock_generates_yml(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout=(
                '{"python_version":"3.11.8","rdkit_version":"2024.09.6",'
                '"meeko_version":"0.7.1","numpy_version":"2.2.0",'
                '"scipy_version":"1.15.0","platform":"Windows-test"}'
            ),
            stderr="",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "repo"
            env_dir = Path(temp_dir) / "Miniconda3" / "envs" / "dockstart-rdkit-meeko"
            env_dir.mkdir(parents=True)
            (env_dir / "conda-meta").mkdir()
            python_path = env_dir / "python.exe"
            python_path.write_text("fake python", encoding="utf-8")
            output_path = repo_root / "docs" / "release" / "environment-dockstart-rdkit-meeko.yml"

            with patch("export_toolchain_environment.subprocess.run", return_value=completed) as run_mock:
                response = export_toolchain_environment(
                    repo_root=repo_root,
                    python_path=str(python_path),
                    output_path=output_path,
                )

            self.assertTrue(response["ok"])
            self.assertEqual(response["status"], "exported")
            self.assertTrue(output_path.is_file())
            content = output_path.read_text(encoding="utf-8")
            self.assertIn("name: dockstart-rdkit-meeko", content)
            self.assertIn("python=3.11", content)
            self.assertIn("rdkit=2024.09.6", content)
            self.assertEqual(run_mock.call_args[0][0][0], str(python_path.resolve()))

    def test_build_environment_yml_keeps_recommended_packages(self) -> None:
        yml = build_environment_yml(
            {
                "python_version": "3.11.8",
                "rdkit_version": "",
                "meeko_version": "",
                "numpy_version": "",
                "scipy_version": "",
                "platform": "Windows-test",
                "generated_at": "2099-01-01T00:00:00+00:00",
            }
        )

        self.assertIn("  - python=3.11", yml)
        self.assertIn("  - rdkit", yml)
        self.assertIn("  - meeko", yml)
        self.assertIn("  - numpy", yml)
        self.assertIn("  - scipy", yml)


if __name__ == "__main__":
    unittest.main()
