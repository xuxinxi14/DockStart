from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import diagnostics  # noqa: E402
from dockstart_core.models import ToolCheckResult  # noqa: E402


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


def toolchain_payload(vina: str = "ok", python: str = "ok", rdkit: str = "ok", meeko: str = "ok") -> dict[str, object]:
    return {
        "runtime_mode": "dev",
        "resource_dir": "",
        "toolchain_root": "mock-resources",
        "active_vina": tool("vina", vina),
        "resolved_python": tool("python", python, "configured"),
        "rdkit_for_python": tool("rdkit", rdkit, "configured"),
        "meeko_for_python": tool("meeko", meeko, "configured"),
    }


def capability_payload(
    basic: bool = True,
    assisted: bool = True,
    demo: bool = True,
    recommended: str = "assisted",
) -> dict[str, object]:
    return {
        "viewer_status": tool("viewer", "ok", "frontend_dependency"),
        "basic_mode_available": basic,
        "assisted_mode_available": assisted,
        "demo_mode_available": demo,
        "recommended_mode": recommended,
        "next_action": "mock next action",
    }


def demo_payload(available: bool = True) -> dict[str, object]:
    return {
        "ok": True,
        "demos": [
            {
                "demo_type": "basic_pdbqt",
                "title": "Basic demo",
                "exists": available,
            }
        ],
    }


class DiagnosticsTests(unittest.TestCase):
    def test_post_install_check_reports_missing_vina(self) -> None:
        with (
            patch.object(diagnostics, "get_toolchain_status", return_value=toolchain_payload(vina="missing")),
            patch.object(diagnostics, "get_app_capability_profile", return_value=capability_payload(basic=False, assisted=False)),
            patch.object(diagnostics, "list_available_demo_projects", return_value=demo_payload()),
        ):
            response = diagnostics.run_post_install_check()

        self.assertTrue(response["ok"])
        self.assertFalse(response["modes"]["basic_mode_available"])
        self.assertTrue(any("AutoDock Vina" in issue for issue in response["issues"]))

    def test_post_install_check_reports_missing_rdkit_meeko(self) -> None:
        with (
            patch.object(diagnostics, "get_toolchain_status", return_value=toolchain_payload(rdkit="missing", meeko="missing")),
            patch.object(diagnostics, "get_app_capability_profile", return_value=capability_payload(assisted=False, recommended="basic")),
            patch.object(diagnostics, "list_available_demo_projects", return_value=demo_payload()),
        ):
            response = diagnostics.run_post_install_check()

        self.assertTrue(response["ok"])
        self.assertFalse(response["modes"]["assisted_mode_available"])
        self.assertTrue(any("RDKit/Meeko" in issue for issue in response["issues"]))

    def test_post_install_check_reports_missing_demo_projects(self) -> None:
        with (
            patch.object(diagnostics, "get_toolchain_status", return_value=toolchain_payload()),
            patch.object(diagnostics, "get_app_capability_profile", return_value=capability_payload(demo=False)),
            patch.object(diagnostics, "list_available_demo_projects", return_value=demo_payload(available=False)),
        ):
            response = diagnostics.run_post_install_check()

        self.assertTrue(response["ok"])
        self.assertFalse(response["demo_projects"]["available"])
        self.assertTrue(any("示例项目" in issue for issue in response["issues"]))

    def test_export_diagnostic_report_writes_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(diagnostics, "get_toolchain_status", return_value=toolchain_payload()),
                patch.object(diagnostics, "get_app_capability_profile", return_value=capability_payload()),
                patch.object(diagnostics, "list_available_demo_projects", return_value=demo_payload()),
            ):
                response = diagnostics.export_diagnostic_report(temp_dir)

            report_file = Path(response["report_file"])
            self.assertTrue(report_file.is_file())
            content = report_file.read_text(encoding="utf-8")
            self.assertIn("DockStart 诊断报告", content)
            self.assertIn("AutoDock Vina", content)
            self.assertIn("不会上传网络", content)


if __name__ == "__main__":
    unittest.main()
