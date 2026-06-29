from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import capabilities  # noqa: E402
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import create_project, import_ligand_pdbqt, import_receptor_pdbqt  # noqa: E402
from dockstart_core.toolchain_paths import TOOLCHAIN_ROOT_ENV_VAR  # noqa: E402


def tool(key: str, status: str, source: str = "auto") -> dict[str, object]:
    return ToolCheckResult(
        key=key,
        name=key,
        status=status,  # type: ignore[arg-type]
        version="1.0.0" if status == "ok" else "",
        path=f"mock-{key}" if status == "ok" else "",
        message="mock",
        source=source,  # type: ignore[arg-type]
    ).to_dict()


def toolchain_payload(
    vina: str = "ok",
    python: str = "ok",
    rdkit: str = "ok",
    meeko: str = "ok",
) -> dict[str, object]:
    return {
        "active_vina": tool("vina", vina),
        "resolved_python": tool("python", python, "configured"),
        "rdkit_for_python": tool("rdkit", rdkit, "configured"),
        "meeko_for_python": tool("meeko", meeko, "configured"),
    }


class CapabilityProfileTests(unittest.TestCase):
    def _create_demo_resource(self, root: Path) -> None:
        demo_dir = root / "examples" / "demo_basic_project"
        demo_dir.mkdir(parents=True)
        (demo_dir / "project.json").write_text('{"project_name":"demo_basic_project"}\n', encoding="utf-8")

    def _create_project_with_pdbqt(self, base_dir: str) -> Path:
        response = create_project("basic_project", base_dir)
        project_dir = Path(response["project_dir"])
        receptor = Path(base_dir) / "receptor.pdbqt"
        ligand = Path(base_dir) / "ligand.pdbqt"
        receptor.write_text("REMARK receptor\n", encoding="utf-8")
        ligand.write_text("REMARK ligand\n", encoding="utf-8")
        self.assertTrue(import_receptor_pdbqt(str(project_dir), str(receptor))["ok"])
        self.assertTrue(import_ligand_pdbqt(str(project_dir), str(ligand))["ok"])
        return project_dir

    def test_profile_marks_basic_available_without_rdkit_meeko(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._create_demo_resource(root)
            with (
                patch.dict(os.environ, {TOOLCHAIN_ROOT_ENV_VAR: str(root)}, clear=False),
                patch.object(capabilities, "get_toolchain_status", return_value=toolchain_payload(rdkit="missing", meeko="missing")),
                patch.object(capabilities.viewer_adapter, "detect", return_value=ToolCheckResult("viewer", "3Dmol.js", "ok")),
            ):
                profile = capabilities.get_app_capability_profile()

        self.assertTrue(profile["basic_mode_available"])
        self.assertFalse(profile["assisted_mode_available"])
        self.assertTrue(profile["demo_mode_available"])
        self.assertEqual(profile["recommended_mode"], "basic")
        self.assertIn("Basic Mode", profile["next_action"])

    def test_profile_recommends_assisted_when_full_toolchain_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._create_demo_resource(root)
            with (
                patch.dict(os.environ, {TOOLCHAIN_ROOT_ENV_VAR: str(root)}, clear=False),
                patch.object(capabilities, "get_toolchain_status", return_value=toolchain_payload()),
                patch.object(capabilities.viewer_adapter, "detect", return_value=ToolCheckResult("viewer", "3Dmol.js", "ok")),
            ):
                profile = capabilities.get_app_capability_profile()

        self.assertTrue(profile["basic_mode_available"])
        self.assertTrue(profile["assisted_mode_available"])
        self.assertEqual(profile["recommended_mode"], "assisted")

    def test_profile_recommends_demo_when_vina_missing_but_demo_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._create_demo_resource(root)
            with (
                patch.dict(os.environ, {TOOLCHAIN_ROOT_ENV_VAR: str(root)}, clear=False),
                patch.object(capabilities, "get_toolchain_status", return_value=toolchain_payload(vina="missing")),
                patch.object(capabilities.viewer_adapter, "detect", return_value=ToolCheckResult("viewer", "3Dmol.js", "ok")),
            ):
                profile = capabilities.get_app_capability_profile()

        self.assertFalse(profile["basic_mode_available"])
        self.assertFalse(profile["assisted_mode_available"])
        self.assertTrue(profile["demo_mode_available"])
        self.assertEqual(profile["recommended_mode"], "demo")
        self.assertIn("示例项目", profile["next_action"])

    def test_minimum_requirements_allow_basic_mode_without_rdkit_meeko(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_pdbqt(temp_dir)
            fake_profile = {
                "basic_mode_available": True,
                "assisted_mode_available": False,
                "demo_mode_available": False,
                "blocking_items": [
                    {"mode": "assisted", "item": "rdkit", "message": "Assisted Mode 需要 RDKit 可用。"},
                    {"mode": "assisted", "item": "meeko", "message": "Assisted Mode 需要 Meeko 可用。"},
                ],
                "demo_projects": [],
            }
            with patch.object(capabilities, "get_app_capability_profile", return_value=fake_profile):
                status = capabilities.get_minimum_requirements_status(str(project_dir))

        self.assertTrue(status["ok"])
        self.assertTrue(status["basic_mode"]["ready"])
        self.assertFalse(status["assisted_mode"]["ready"])
        self.assertIn("Basic Mode", status["next_action"])

    def test_project_recommendation_prefers_basic_when_pdbqt_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_pdbqt(temp_dir)
            fake_profile = {
                "basic_mode_available": True,
                "assisted_mode_available": True,
                "demo_mode_available": True,
                "blocking_items": [],
                "demo_projects": [],
            }
            with patch.object(capabilities, "get_app_capability_profile", return_value=fake_profile):
                recommendation = capabilities.get_project_mode_recommendation(str(project_dir))

        self.assertTrue(recommendation["ok"])
        self.assertEqual(recommendation["recommended_mode"], "basic")
        self.assertIn("PDBQT", recommendation["reason"])


if __name__ == "__main__":
    unittest.main()
