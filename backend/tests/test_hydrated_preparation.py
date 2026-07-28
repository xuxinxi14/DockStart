from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import hydrated  # noqa: E402
from dockstart_core.project import create_project  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atom_line(
    serial: int,
    name: str,
    x: float,
    y: float,
    z: float,
    atom_type: str,
) -> str:
    return (
        f"ATOM  {serial:5d} {name:^4} LIG A   1    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}"
        f"{1.00:6.2f}{0.00:6.2f}    "
        f"{0.000:6.3f} {atom_type:>2}\n"
    )


def _hydrated_pdbqt(water_count: int = 2) -> str:
    lines = [
        "ROOT\n",
        _atom_line(1, "C1", 0.0, 0.0, 0.0, "C"),
        _atom_line(2, "O1", 1.2, 0.0, 0.0, "OA"),
    ]
    for index in range(water_count):
        lines.append(
            _atom_line(
                3 + index,
                "WAT",
                2.0 + index,
                1.0,
                0.5,
                "W",
            )
        )
    lines.extend(["ENDROOT\n", "TORSDOF 0\n"])
    return "".join(lines)


def _added_h_sdf() -> str:
    return """DockStart hydrated
  DockStart          3D

  2  1  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2000    0.0000    0.4000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0  0  0  0
M  END
$$$$
"""


class FakeHydratedRunner:
    def __init__(
        self,
        *,
        rdkit_module: Path,
        meeko_module: Path,
        water_count: int = 2,
        probe_ok: bool = True,
        after_prepare: object | None = None,
    ) -> None:
        self.rdkit_module = rdkit_module
        self.meeko_module = meeko_module
        self.water_count = water_count
        self.probe_ok = probe_ok
        self.after_prepare = after_prepare
        self.commands: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        cwd: str | Path,
        timeout: int = 300,
    ) -> SimpleNamespace:
        _ = cwd, timeout
        normalized = [str(item) for item in command]
        self.commands.append(normalized)
        mode = normalized[4]
        if mode == "probe":
            if not self.probe_ok:
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr='{"ok": false, "message": "hydrate unsupported"}\n',
                )
            payload = {
                "ok": True,
                "rdkit_version": "2026.3.3-test",
                "rdkit_module_file": str(self.rdkit_module),
                "meeko_version": "0.7.1-test",
                "meeko_module_file": str(self.meeko_module),
                "writer_interface": "PDBQTWriterLegacy",
                "hydrate_supported": True,
                "probe_water_count": 3,
            }
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(payload) + "\n",
                stderr="",
            )
        if mode != "prepare":
            raise AssertionError(normalized)
        Path(normalized[6]).write_text(_added_h_sdf(), encoding="utf-8")
        Path(normalized[7]).write_text(
            _hydrated_pdbqt(self.water_count),
            encoding="utf-8",
        )
        if callable(self.after_prepare):
            self.after_prepare()
        payload = {
            "ok": True,
            "rdkit_version": "2026.3.3-test",
            "meeko_version": "0.7.1-test",
            "writer_interface": "PDBQTWriterLegacy",
            "atom_count": 6,
            "bond_count": 5,
            "water_count": self.water_count,
        }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload) + "\n",
            stderr="",
        )


class HydratedLigandPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        created = create_project("hydrated_test", str(self.base))
        self.assertTrue(created["ok"], created)
        self.project_dir = Path(created["project_dir"])
        self.raw_file = self.project_dir / "raw" / "ligand.sdf"
        self.raw_file.write_text(_added_h_sdf(), encoding="utf-8")
        self.standard_ligand = self.project_dir / "prepared" / "ligand.pdbqt"
        self.standard_ligand.write_text("STANDARD LIGAND\n", encoding="utf-8")

        project_json = self.project_dir / "project.json"
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data["ligand"]["raw_file"] = "raw/ligand.sdf"
        data["ligand"]["file"] = "prepared/ligand.pdbqt"
        data["docking_protocol"] = {
            "engine": "vina",
            "protocol_id": "rigid_single",
        }
        data["box"] = {
            "center_x": 3.0,
            "center_y": 4.0,
            "center_z": 5.0,
            "size_x": 20.0,
            "size_y": 21.0,
            "size_z": 22.0,
        }
        project_json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.tool_root = self.base / "fake_tools"
        self.tool_root.mkdir()
        self.python_file = self.tool_root / "python.exe"
        self.rdkit_module = self.tool_root / "rdkit_init.py"
        self.meeko_module = self.tool_root / "meeko_init.py"
        self.python_file.write_bytes(b"python-tool-v1")
        self.rdkit_module.write_bytes(b"rdkit-tool-v1")
        self.meeko_module.write_bytes(b"meeko-tool-v1")

    def _tools(self) -> dict:
        return {
            "python": {
                "status": "ok",
                "version": "Python 3.11.15",
                "path": str(self.python_file),
                "source": "bundled",
            },
            "rdkit": {
                "status": "ok",
                "version": "2026.3.3-test",
                "capabilities": {
                    "import": {"status": "ok"},
                    "sdf_inline_read": {"status": "ok"},
                },
            },
            "meeko": {
                "status": "ok",
                "version": "0.7.1-test",
                "capabilities": {
                    "import": {"status": "ok"},
                    "ligand_preparation": {"status": "ok"},
                },
            },
        }

    def _prepare(self, runner: FakeHydratedRunner | None = None) -> dict:
        selected = runner or FakeHydratedRunner(
            rdkit_module=self.rdkit_module,
            meeko_module=self.meeko_module,
        )
        with (
            patch("dockstart_core.hydrated._tool_status", return_value=self._tools()),
            patch(
                "dockstart_core.hydrated.meeko_adapter.run_preparation_command",
                side_effect=selected,
            ),
        ):
            return hydrated.prepare_hydrated_ligand(str(self.project_dir))

    def _project_data(self) -> dict:
        return json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )

    def _active_manifest(self) -> tuple[Path, dict]:
        data = self._project_data()
        pointer = data["hydrated_docking"]["active_ligand_manifest"]
        manifest_path = self.project_dir / pointer["path"]
        return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_success_is_independent_and_freezes_auditable_evidence(self) -> None:
        original = self._project_data()
        runner = FakeHydratedRunner(
            rdkit_module=self.rdkit_module,
            meeko_module=self.meeko_module,
        )
        response = self._prepare(runner)
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["preparation_ready"], response)
        self.assertEqual(response["protocol_id"], hydrated.PROTOCOL_ID)
        self.assertEqual(response["water_count"], 2)

        current = self._project_data()
        self.assertEqual(current["ligand"], original["ligand"])
        self.assertEqual(current["docking_protocol"], original["docking_protocol"])
        self.assertEqual(current["box"], original["box"])
        self.assertEqual(self.standard_ligand.read_text(encoding="utf-8"), "STANDARD LIGAND\n")
        self.assertEqual(
            set(current["hydrated_docking"]),
            {"active_ligand_manifest", "updated_at"},
        )

        pointer = current["hydrated_docking"]["active_ligand_manifest"]
        self.assertEqual(set(pointer), {"path", "sha256"})
        manifest_path, manifest = self._active_manifest()
        self.assertEqual(pointer["sha256"], _sha256(manifest_path))
        self.assertEqual(manifest["status"], "finished")
        self.assertEqual(manifest["protocol_id"], "hydrated_ad4_experimental")
        self.assertEqual(manifest["source"]["sha256"], _sha256(self.raw_file))
        self.assertEqual(manifest["script"]["sha256"], _sha256(manifest_path.parent / "prepare_hydrated_ligand.py"))
        self.assertEqual(manifest["tools"]["rdkit_version"], "2026.3.3-test")
        self.assertEqual(manifest["tools"]["meeko_version"], "0.7.1-test")
        self.assertTrue(manifest["integrity"]["source_unchanged"])
        self.assertTrue(manifest["integrity"]["tools_unchanged"])
        self.assertTrue(manifest["integrity"]["script_unchanged"])
        self.assertEqual(manifest["outputs"]["hydrated_pdbqt"]["water_count"], 2)
        self.assertEqual(
            manifest["outputs"]["hydrated_pdbqt"]["sha256"],
            _sha256(self.project_dir / manifest["outputs"]["hydrated_pdbqt"]["path"]),
        )
        self.assertEqual(
            manifest["outputs"]["added_h_sdf"]["sha256"],
            _sha256(self.project_dir / manifest["outputs"]["added_h_sdf"]["path"]),
        )
        self.assertEqual(runner.commands[0][1:4], ["-I", "-B", str(manifest_path.parent / "prepare_hydrated_ligand.py")])
        self.assertEqual(runner.commands[0][4], "probe")
        self.assertEqual(runner.commands[1][4], "prepare")
        self.assertNotIn(
            "shell",
            " ".join(item for command in runner.commands for item in command).lower(),
        )

        status = hydrated.get_status(str(self.project_dir))
        self.assertTrue(status["valid"], status)
        self.assertEqual(status["status"], "ready")

    def test_rejects_non_project_empty_unsupported_and_oversized_sources(self) -> None:
        project_json = self.project_dir / "project.json"
        outside = self.base / "outside.sdf"
        outside.write_text(_added_h_sdf(), encoding="utf-8")
        cases = [
            ("outside", str(outside), None),
            ("unsupported", "raw/ligand.pdb", None),
            ("empty", "raw/empty.sdf", b""),
            ("oversized", "raw/large.sdf", b"12345"),
        ]
        for name, raw_value, payload in cases:
            with self.subTest(name=name):
                data = json.loads(project_json.read_text(encoding="utf-8"))
                data["ligand"]["raw_file"] = raw_value
                project_json.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                if payload is not None:
                    (self.project_dir / raw_value).write_bytes(payload)
                size_patch = (
                    patch.object(hydrated, "MAX_RAW_LIGAND_BYTES", 4)
                    if name == "oversized"
                    else patch.object(
                        hydrated,
                        "MAX_RAW_LIGAND_BYTES",
                        hydrated.MAX_RAW_LIGAND_BYTES,
                    )
                )
                with size_patch:
                    response = hydrated.prepare_hydrated_ligand(str(self.project_dir))
                self.assertFalse(response["ok"], response)
                self.assertNotIn("hydrated_docking", self._project_data())

    def test_rejects_linked_source_when_platform_allows_symlinks(self) -> None:
        outside = self.base / "outside-linked.sdf"
        outside.write_text(_added_h_sdf(), encoding="utf-8")
        linked = self.project_dir / "raw" / "linked.sdf"
        try:
            os.symlink(outside, linked)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        data = self._project_data()
        data["ligand"]["raw_file"] = "raw/linked.sdf"
        (self.project_dir / "project.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        response = hydrated.prepare_hydrated_ligand(str(self.project_dir))
        self.assertFalse(response["ok"], response)
        self.assertEqual(
            response["error"]["code"],
            "HYDRATED_LIGAND_SOURCE_INVALID",
        )

    def test_tool_gate_requires_real_detected_capabilities(self) -> None:
        tools = self._tools()
        tools["meeko"]["capabilities"]["ligand_preparation"]["status"] = "unknown"
        with patch("dockstart_core.hydrated._tool_status", return_value=tools):
            response = hydrated.prepare_hydrated_ligand(str(self.project_dir))
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["error"]["code"], "HYDRATED_TOOLS_NOT_READY")
        self.assertFalse((self.project_dir / hydrated.PREPARATION_ROOT).exists())

    def test_hydrate_probe_failure_leaves_failed_audit_manifest(self) -> None:
        runner = FakeHydratedRunner(
            rdkit_module=self.rdkit_module,
            meeko_module=self.meeko_module,
            probe_ok=False,
        )
        response = self._prepare(runner)
        self.assertFalse(response["ok"], response)
        self.assertEqual(
            response["error"]["code"],
            "HYDRATED_MEEKO_CAPABILITY_MISSING",
        )
        manifests = list(
            (self.project_dir / hydrated.PREPARATION_ROOT).glob(
                "hydrated_ligand_*/manifest.json"
            )
        )
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(
            manifest["error"]["code"],
            "HYDRATED_MEEKO_CAPABILITY_MISSING",
        )
        self.assertNotIn("hydrated_docking", self._project_data())

    def test_zero_w_atoms_blocks_publication_and_keeps_audit(self) -> None:
        runner = FakeHydratedRunner(
            rdkit_module=self.rdkit_module,
            meeko_module=self.meeko_module,
            water_count=0,
        )
        response = self._prepare(runner)
        self.assertFalse(response["ok"], response)
        manifests = list(
            (self.project_dir / hydrated.PREPARATION_ROOT).glob(
                "hydrated_ligand_*/manifest.json"
            )
        )
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "failed")
        self.assertNotIn("hydrated_docking", self._project_data())
        self.assertFalse((manifests[0].parent / "ligand_hydrated.pdbqt").exists())

    def test_source_change_during_preparation_blocks_activation(self) -> None:
        runner = FakeHydratedRunner(
            rdkit_module=self.rdkit_module,
            meeko_module=self.meeko_module,
            after_prepare=lambda: self.raw_file.write_text(
                _added_h_sdf() + "\nchanged\n",
                encoding="utf-8",
            ),
        )
        response = self._prepare(runner)
        self.assertFalse(response["ok"], response)
        self.assertEqual(
            response["error"]["code"],
            "HYDRATED_LIGAND_SOURCE_CHANGED",
        )
        self.assertNotIn("hydrated_docking", self._project_data())

    def test_tool_change_during_preparation_blocks_activation(self) -> None:
        runner = FakeHydratedRunner(
            rdkit_module=self.rdkit_module,
            meeko_module=self.meeko_module,
            after_prepare=lambda: self.meeko_module.write_bytes(b"meeko-tool-v2"),
        )
        response = self._prepare(runner)
        self.assertFalse(response["ok"], response)
        self.assertEqual(
            response["error"]["code"],
            "HYDRATED_PREPARATION_TOOL_CHANGED",
        )
        self.assertNotIn("hydrated_docking", self._project_data())

    def test_status_invalidates_source_output_and_manifest_tampering(self) -> None:
        for changed in ("source", "output", "manifest"):
            with self.subTest(changed=changed):
                with tempfile.TemporaryDirectory() as temp_dir:
                    created = create_project(f"tamper_{changed}", temp_dir)
                    self.assertTrue(created["ok"], created)
                    project_dir = Path(created["project_dir"])
                    raw = project_dir / "raw" / "ligand.sdf"
                    raw.write_text(_added_h_sdf(), encoding="utf-8")
                    data = json.loads(
                        (project_dir / "project.json").read_text(encoding="utf-8")
                    )
                    data["ligand"]["raw_file"] = "raw/ligand.sdf"
                    (project_dir / "project.json").write_text(
                        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    runner = FakeHydratedRunner(
                        rdkit_module=self.rdkit_module,
                        meeko_module=self.meeko_module,
                    )
                    with (
                        patch(
                            "dockstart_core.hydrated._tool_status",
                            return_value=self._tools(),
                        ),
                        patch(
                            "dockstart_core.hydrated.meeko_adapter.run_preparation_command",
                            side_effect=runner,
                        ),
                    ):
                        prepared = hydrated.prepare_hydrated_ligand(str(project_dir))
                    self.assertTrue(prepared["ok"], prepared)
                    current = json.loads(
                        (project_dir / "project.json").read_text(encoding="utf-8")
                    )
                    pointer = current["hydrated_docking"]["active_ligand_manifest"]
                    manifest_path = project_dir / pointer["path"]
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if changed == "source":
                        raw.write_text(_added_h_sdf() + "\nsource changed\n", encoding="utf-8")
                    elif changed == "output":
                        output = project_dir / manifest["outputs"]["hydrated_pdbqt"]["path"]
                        output.write_text(output.read_text(encoding="utf-8") + "\nREMARK changed\n", encoding="utf-8")
                    else:
                        manifest["finished_at"] = "tampered"
                        manifest_path.write_text(
                            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                    status = hydrated.get_status(str(project_dir))
                    self.assertTrue(status["ok"], status)
                    self.assertFalse(status["valid"], status)
                    self.assertEqual(status["status"], "invalid")
                    self.assertTrue(status["issues"])

    def test_status_rejects_manifest_pointer_outside_protocol_root(self) -> None:
        outside_manifest = self.project_dir / "raw" / "manifest.json"
        outside_manifest.write_text("{}\n", encoding="utf-8")
        data = self._project_data()
        data["hydrated_docking"] = {
            "active_ligand_manifest": {
                "path": "raw/manifest.json",
                "sha256": _sha256(outside_manifest),
            },
            "updated_at": "2026-07-28T00:00:00+00:00",
        }
        (self.project_dir / "project.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        status = hydrated.get_status(str(self.project_dir))
        self.assertTrue(status["ok"], status)
        self.assertFalse(status["valid"], status)
        self.assertEqual(status["status"], "invalid")

    def test_generated_script_requires_3d_coordinates_when_assisted_runtime_exists(self) -> None:
        bundled_python = BACKEND_ROOT.parent / "resources" / "python" / "python.exe"
        if not bundled_python.is_file():
            self.skipTest("Assisted runtime is not available")
        record_dir = self.base / "real_script_probe"
        record_dir.mkdir()
        script = record_dir / "prepare_hydrated_ligand.py"
        script.write_text(hydrated._generated_script_text(), encoding="utf-8")
        two_dimensional = record_dir / "ligand_2d.mol"
        two_dimensional.write_text(
            _added_h_sdf()
            .replace("          3D", "          2D")
            .replace(
                "    1.2000    0.0000    0.4000 O",
                "    1.2000    0.0000    0.0000 O",
            )
            .split("$$$$", 1)[0],
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                str(bundled_python),
                "-I",
                "-B",
                str(script),
                "prepare",
                str(two_dimensional),
                str(record_dir / "added_h.sdf"),
                str(record_dir / "hydrated.pdbqt"),
            ],
            cwd=record_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("3D", completed.stderr)

        three_dimensional = record_dir / "ligand_3d.sdf"
        three_dimensional.write_text(_added_h_sdf(), encoding="utf-8")
        added_h = record_dir / "valid_added_h.sdf"
        hydrated_pdbqt = record_dir / "valid_hydrated.pdbqt"
        prepared = subprocess.run(
            [
                str(bundled_python),
                "-I",
                "-B",
                str(script),
                "prepare",
                str(three_dimensional),
                str(added_h),
                str(hydrated_pdbqt),
            ],
            cwd=record_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertTrue(added_h.is_file())
        self.assertGreater(
            sum(
                1
                for line in hydrated_pdbqt.read_text(encoding="utf-8").splitlines()
                if (line.startswith("ATOM") or line.startswith("HETATM"))
                and line.split()[-1] == "W"
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
