from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core import project as project_module  # noqa: E402
from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core.preparation import _ligand_preparation_script_text, prepare_ligand_pdbqt  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    CURRENT_PROJECT_SCHEMA_VERSION,
    _preparation_target_lock,
    _project_from_dict,
    analyze_vina_run_results,
    create_project,
    execute_prepared_vina_run,
    export_markdown_report,
    load_project,
    prepare_vina_run,
    recover_project_state,
    save_project,
    update_run_metadata,
)
from dockstart_core.settings import (  # noqa: E402
    SETTINGS_ENV_VAR,
    DockStartSettings,
    ToolPaths,
    load_settings,
    save_settings,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _meeko_tools() -> dict:
    return {
        "ok": True,
        "tools": {
            "python": {
                "status": "ok",
                "path": sys.executable,
                "source": "current_environment",
            },
            "rdkit": {"status": "ok", "version": "test"},
            "meeko": {
                "status": "ok",
                "version": "test",
                "capabilities": {
                    "ligand_preparation": {"status": "ok"},
                    "receptor_preparation": {
                        "status": "ok",
                        "module_candidates_found": ["meeko.cli.mk_prepare_receptor"],
                    },
                },
            },
        },
    }


class BackendReliabilityTests(unittest.TestCase):
    def _create_project(self, base_dir: str, name: str = "reliability") -> Path:
        created = create_project(name, base_dir)
        self.assertTrue(created["ok"], created)
        return Path(created["project_dir"])

    def _set_ligand_raw(self, project_dir: Path, *, output_text: str | None = None) -> None:
        (project_dir / "raw" / "ligand.sdf").write_text("mock sdf\n", encoding="utf-8")
        project_json = project_dir / "project.json"
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data["ligand"]["raw_file"] = "raw/ligand.sdf"
        if output_text is not None:
            (project_dir / "prepared" / "ligand.pdbqt").write_text(output_text, encoding="utf-8")
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
        project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _prepare_fake_vina_project(self, project_dir: Path) -> tuple[Path, ToolCheckResult, str]:
        receptor = project_dir / "prepared" / "receptor.pdbqt"
        ligand = project_dir / "prepared" / "ligand.pdbqt"
        config = project_dir / "configs" / "vina_config.txt"
        receptor.write_text("ATOM receptor\n", encoding="utf-8")
        ligand.write_text("ATOM ligand\nTORSDOF 0\n", encoding="utf-8")
        config.write_text(
            "receptor = prepared/receptor.pdbqt\nligand = prepared/ligand.pdbqt\n",
            encoding="utf-8",
        )
        fake_vina = project_dir / "fake-vina.exe"
        fake_vina.write_bytes(b"vina-before")
        project_json = project_dir / "project.json"
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data["receptor"]["file"] = "prepared/receptor.pdbqt"
        data["ligand"]["file"] = "prepared/ligand.pdbqt"
        data["config"] = {
            "vina_config_file": "configs/vina_config.txt",
            "generated_at": "2026-07-14T00:00:00+00:00",
        }
        project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        detection = ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version="1.2.7",
            path=str(fake_vina),
            source="configured",
        )
        with patch("dockstart_core.project.vina_adapter.detect", return_value=detection):
            prepared = prepare_vina_run(str(project_dir))
        self.assertTrue(prepared["ok"], prepared)
        return fake_vina, detection, str(prepared["run_id"])

    def test_revision_rejects_stale_copy_even_when_timestamp_is_same(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            loaded = load_project(str(project_dir))
            first = _project_from_dict(loaded["project"], project_dir)
            second = _project_from_dict(loaded["project"], project_dir)
            first.box.center_x = 11
            second.box.center_x = 22

            with patch("dockstart_core.project._now_iso", return_value="2026-07-14T00:00:00+00:00"):
                first_save = save_project(first)
                stale_save = save_project(second)

            persisted = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertTrue(first_save["ok"], first_save)
            self.assertFalse(stale_save["ok"])
            self.assertEqual(stale_save["error"]["code"], "PROJECT_SAVE_CONFLICT")
            self.assertEqual(persisted["box"]["center_x"], 11)
            self.assertEqual(persisted["revision"], first.revision)

    def test_unversioned_project_migrates_once_and_preserves_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data.pop("schema_version")
            data.pop("revision")
            data["plugin_owned"] = {"future": True}
            data["receptor"]["custom_annotation"] = "keep-me"
            project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            first = load_project(str(project_dir))
            second = load_project(str(project_dir))
            migrated = json.loads(project_json.read_text(encoding="utf-8"))
            backups = list(project_dir.glob("project.json.schema-v0.bak*"))

            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            self.assertEqual(migrated["schema_version"], CURRENT_PROJECT_SCHEMA_VERSION)
            self.assertEqual(migrated["revision"], 0)
            self.assertEqual(migrated["plugin_owned"], {"future": True})
            self.assertEqual(migrated["receptor"]["custom_annotation"], "keep-me")
            self.assertEqual(len(backups), 1)

            model = _project_from_dict(second["project"], project_dir)
            model.box.center_y = 7
            saved = save_project(model)
            after_save = json.loads(project_json.read_text(encoding="utf-8"))
            self.assertTrue(saved["ok"], saved)
            self.assertEqual(after_save["plugin_owned"], {"future": True})
            self.assertEqual(after_save["receptor"]["custom_annotation"], "keep-me")

    def test_future_project_schema_is_rejected_without_modification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION + 10
            original = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            project_json.write_text(original, encoding="utf-8")

            loaded = load_project(str(project_dir))

            self.assertFalse(loaded["ok"])
            self.assertEqual(loaded["error"]["code"], "PROJECT_SCHEMA_VERSION_UNSUPPORTED")
            self.assertEqual(project_json.read_text(encoding="utf-8"), original)
            self.assertEqual(list(project_dir.glob("project.json.schema-v*.bak*")), [])

    def test_migration_validates_known_fields_before_writing_backup_or_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data.pop("schema_version")
            data["box"]["size_x"] = "not-a-number"
            original = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            project_json.write_text(original, encoding="utf-8")

            loaded = load_project(str(project_dir))

            self.assertFalse(loaded["ok"])
            self.assertEqual(project_json.read_text(encoding="utf-8"), original)
            self.assertEqual(list(project_dir.glob("project.json.schema-v*.bak*")), [])

    def test_copied_project_uses_opened_directory_not_stored_absolute_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original = self._create_project(temp_dir, "original")
            copied = Path(temp_dir) / "copied-on-another-drive"
            shutil.copytree(original, copied)
            project_json = copied / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["project_dir"] = r"Z:\stale-machine\DockStart\original"
            project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            loaded = load_project(str(copied))
            model = _project_from_dict(loaded["project"], copied)
            model.box.center_x = 9
            saved = save_project(model)
            persisted = json.loads(project_json.read_text(encoding="utf-8"))

            self.assertTrue(loaded["ok"], loaded)
            self.assertTrue(saved["ok"], saved)
            self.assertEqual(Path(loaded["project_dir"]), copied.resolve())
            self.assertEqual(Path(loaded["project"]["project_dir"]), copied.resolve())
            self.assertEqual(Path(persisted["project_dir"]), copied.resolve())
            self.assertEqual(persisted["box"]["center_x"], 9)

    def test_recovery_reconciles_terminal_metadata_and_dead_running_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["runs"] = [
                {"run_id": "run_001", "status": "running"},
                {"run_id": "run_002", "status": "running"},
            ]
            project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            for run_id in ("run_001", "run_002"):
                (project_dir / "runs" / run_id).mkdir(parents=True)
            (project_dir / "runs" / "run_001" / "metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run_001",
                        "status": "finished",
                        "stage": "finished",
                        "finished_at": "2026-07-14T00:00:00+00:00",
                        "exit_code": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (project_dir / "runs" / "run_002" / "metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run_002",
                        "status": "running",
                        "stage": "running",
                        "started_at": "2020-01-01T00:00:00+00:00",
                        "pid": 999999,
                        "trusted_executable": "missing-vina.exe",
                        "process_identity": {
                            "pid": 999999,
                            "executable_path": "missing-vina.exe",
                            "creation_token": "dead",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            recovered = recover_project_state(str(project_dir))
            persisted = json.loads(project_json.read_text(encoding="utf-8"))
            summaries = {item["run_id"]: item for item in persisted["runs"]}
            dead_metadata = json.loads(
                (project_dir / "runs" / "run_002" / "metadata.json").read_text(encoding="utf-8"),
            )

            self.assertTrue(recovered["ok"], recovered)
            self.assertEqual(summaries["run_001"]["status"], "finished")
            self.assertEqual(summaries["run_002"]["status"], "interrupted")
            self.assertEqual(dead_metadata["status"], "interrupted")

    def test_recovery_marks_stale_preparation_interrupted_without_publishing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            ligand = data["preparation"]["ligand"]
            ligand.update(
                {
                    "prep_id": "ligand_001",
                    "status": "running",
                    "metadata_file": "preparation/ligand_001/metadata.json",
                    "output_file": "prepared/ligand.pdbqt",
                },
            )
            data["latest_preparation"]["ligand"] = "ligand_001"
            project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record_dir = project_dir / "preparation" / "ligand_001"
            record_dir.mkdir(parents=True)
            (record_dir / "candidate_ligand.pdbqt").write_text("HALF OUTPUT", encoding="utf-8")
            (record_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "prep_id": "ligand_001",
                        "target": "ligand",
                        "status": "running",
                        "process_missing_since": "2020-01-01T00:00:00+00:00",
                        "candidate_output_file": "preparation/ligand_001/candidate_ligand.pdbqt",
                        "output_file": "prepared/ligand.pdbqt",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            recovered = recover_project_state(str(project_dir))
            persisted = json.loads(project_json.read_text(encoding="utf-8"))

            self.assertTrue(recovered["ok"], recovered)
            self.assertEqual(persisted["preparation"]["ligand"]["status"], "interrupted")
            self.assertFalse((project_dir / "prepared" / "ligand.pdbqt").exists())
            self.assertTrue((record_dir / "candidate_ligand.pdbqt").exists())

    def test_recovery_requires_missing_since_grace_before_interrupting_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["preparation"]["ligand"].update(
                {
                    "prep_id": "ligand_001",
                    "status": "running",
                    "metadata_file": "preparation/ligand_001/metadata.json",
                },
            )
            data["latest_preparation"]["ligand"] = "ligand_001"
            project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record_dir = project_dir / "preparation" / "ligand_001"
            record_dir.mkdir(parents=True)
            metadata_path = record_dir / "metadata.json"
            metadata_path.write_text(
                json.dumps({"prep_id": "ligand_001", "target": "ligand", "status": "running"}),
                encoding="utf-8",
            )

            first = recover_project_state(str(project_dir))
            after_first = json.loads(project_json.read_text(encoding="utf-8"))
            probed = json.loads(metadata_path.read_text(encoding="utf-8"))

            self.assertTrue(first["ok"], first)
            self.assertEqual(after_first["preparation"]["ligand"]["status"], "running")
            self.assertIn("process_missing_since", probed)

    def test_same_target_concurrent_preparation_is_single_flight(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls: list[list[str]] = []

        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            calls.append(command)
            Path(command[-1]).write_text("FIRST CANDIDATE", encoding="utf-8")
            started.set()
            self.assertTrue(release.wait(10))
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_ligand_raw(project_dir)
            results: list[dict] = []
            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_meeko_tools()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                worker = threading.Thread(
                    target=lambda: results.append(prepare_ligand_pdbqt(str(project_dir))),
                    daemon=True,
                )
                worker.start()
                self.assertTrue(started.wait(10))
                duplicate = prepare_ligand_pdbqt(str(project_dir))
                release.set()
                worker.join(10)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(calls), 1)
            self.assertFalse(duplicate["ok"])
            self.assertEqual(duplicate["error"]["code"], "PREPARATION_ALREADY_RUNNING")
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["ok"], results[0])
            self.assertEqual((project_dir / "prepared" / "ligand.pdbqt").read_text(encoding="utf-8"), "FIRST CANDIDATE")

    def test_preparation_target_lock_blocks_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            script = "\n".join(
                [
                    "import sys, time",
                    f"sys.path.insert(0, {str(BACKEND_ROOT)!r})",
                    "from dockstart_core.project import _preparation_target_lock",
                    f"project_dir = {str(project_dir)!r}",
                    "with _preparation_target_lock(project_dir, 'ligand'):",
                    "    print('LOCKED', flush=True)",
                    "    time.sleep(1.0)",
                ],
            )
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "LOCKED")  # type: ignore[union-attr]
                started = time.monotonic()
                with _preparation_target_lock(project_dir, "ligand"):
                    elapsed = time.monotonic() - started
                self.assertGreaterEqual(elapsed, 0.6)
            finally:
                process.wait(timeout=5)
                stderr_text = process.stderr.read() if process.stderr is not None else ""
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
                if process.returncode != 0:
                    self.fail(stderr_text)

    def test_superseded_preparation_cannot_publish_candidate(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            Path(command[-1]).write_text("STALE CANDIDATE", encoding="utf-8")
            started.set()
            self.assertTrue(release.wait(10))
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_ligand_raw(project_dir, output_text="CURRENT VERIFIED OUTPUT")
            results: list[dict] = []
            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_meeko_tools()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                worker = threading.Thread(
                    target=lambda: results.append(prepare_ligand_pdbqt(str(project_dir), overwrite=True)),
                    daemon=True,
                )
                worker.start()
                self.assertTrue(started.wait(10))
                loaded = load_project(str(project_dir))
                replacement = _project_from_dict(loaded["project"], project_dir)
                replacement.latest_preparation["ligand"] = "ligand_999"
                replacement.preparation.ligand.prep_id = "ligand_999"
                replacement.preparation.ligand.status = "running"
                self.assertTrue(save_project(replacement)["ok"])
                release.set()
                worker.join(10)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0]["ok"])
            self.assertEqual(results[0]["error"]["code"], "PREPARATION_OWNERSHIP_LOST")
            self.assertEqual(
                (project_dir / "prepared" / "ligand.pdbqt").read_text(encoding="utf-8"),
                "CURRENT VERIFIED OUTPUT",
            )
            stale_metadata = json.loads(
                (project_dir / "preparation" / "ligand_001" / "metadata.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(stale_metadata["status"], "interrupted")
            self.assertFalse(stale_metadata["published"])

    def test_preparation_rejects_reparsed_prepared_preparation_and_record_paths(self) -> None:
        for unsafe_kind in ("prepared", "preparation", "record"):
            with self.subTest(unsafe_kind=unsafe_kind), tempfile.TemporaryDirectory() as temp_dir:
                project_dir = self._create_project(temp_dir)
                self._set_ligand_raw(project_dir)
                outside = Path(temp_dir) / f"outside-{unsafe_kind}"
                outside.mkdir()
                try:
                    if unsafe_kind == "record":
                        link = project_dir / "preparation" / "ligand_001"
                    else:
                        link = project_dir / unsafe_kind
                        shutil.rmtree(link)
                    os.symlink(outside, link, target_is_directory=True)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"当前环境不能创建目录符号链接：{exc}")

                with patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_meeko_tools()):
                    result = prepare_ligand_pdbqt(str(project_dir), overwrite=True)

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "PREPARATION_PATH_UNSAFE")
                self.assertEqual(list(outside.iterdir()), [])

    def test_preparation_keeps_python_hash_captured_at_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_ligand_raw(project_dir)
            fake_python = project_dir / "fake-python.exe"
            fake_python.write_bytes(b"python-before")
            tools = _meeko_tools()
            tools["tools"]["python"]["path"] = str(fake_python)
            launch_hash = _sha256(fake_python)

            def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
                _ = cwd, timeout
                fake_python.write_bytes(b"python-after")
                Path(command[-1]).write_text("CANDIDATE", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=tools),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_ligand_pdbqt(str(project_dir))

            metadata = json.loads(
                (project_dir / "preparation" / "ligand_001" / "metadata.json").read_text(encoding="utf-8"),
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(metadata["python_sha256"], launch_hash)
            self.assertNotEqual(metadata["python_sha256"], _sha256(fake_python))

    def test_run_scores_reports_and_tool_binaries_have_matching_hashes(self) -> None:
        vina_log = """mode |   affinity | dist from best mode\n     | (kcal/mol) | rmsd l.b.| rmsd u.b.\n-----+------------+----------+----------\n   1       -7.4          0          0\n"""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            receptor = project_dir / "prepared" / "receptor.pdbqt"
            ligand = project_dir / "prepared" / "ligand.pdbqt"
            config = project_dir / "configs" / "vina_config.txt"
            fake_vina = project_dir / "fake-vina.exe"
            receptor.write_text("ATOM receptor\n", encoding="utf-8")
            ligand.write_text("ATOM ligand\nTORSDOF 0\n", encoding="utf-8")
            config.write_text("receptor = prepared/receptor.pdbqt\nligand = prepared/ligand.pdbqt\n", encoding="utf-8")
            fake_vina.write_bytes(b"fake vina binary")
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["receptor"]["file"] = "prepared/receptor.pdbqt"
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            data["config"] = {
                "vina_config_file": "configs/vina_config.txt",
                "generated_at": "2026-07-14T00:00:00+00:00",
            }
            project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            detection = ToolCheckResult(
                key="vina",
                name="AutoDock Vina",
                status="ok",
                version="1.2.7",
                path=str(fake_vina),
                source="configured",
            )
            with patch("dockstart_core.project.vina_adapter.detect", return_value=detection):
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)
            run_id = prepared["run_id"]
            run_dir = project_dir / "runs" / run_id
            (run_dir / "out.pdbqt").write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
            (run_dir / "log.txt").write_text(vina_log, encoding="utf-8")
            (run_dir / "stdout.txt").write_text(vina_log, encoding="utf-8")
            (run_dir / "stderr.txt").write_text("", encoding="utf-8")
            metadata_path = run_dir / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update({"status": "finished", "stage": "finished", "exit_code": 0})
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            recovered = recover_project_state(str(project_dir))
            analyzed = analyze_vina_run_results(str(project_dir), run_id)
            reported = export_markdown_report(str(project_dir), run_id)
            final_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

            self.assertTrue(recovered["ok"], recovered)
            self.assertTrue(analyzed["ok"], analyzed)
            self.assertTrue(reported["ok"], reported)
            self.assertEqual(prepared["metadata"]["vina_sha256"], _sha256(fake_vina))
            for key, filename in {
                "out": "out.pdbqt",
                "log": "log.txt",
                "stdout": "stdout.txt",
                "stderr": "stderr.txt",
                "scores": "scores.csv",
                "report": "docking_report.md",
            }.items():
                self.assertEqual(final_metadata["artifacts"][key]["sha256"], _sha256(run_dir / filename))

    def test_recovery_does_not_hash_current_vina_for_historical_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            fake_vina = project_dir / "historical-vina.exe"
            fake_vina.write_bytes(b"bytes-that-exist-now")
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir()
            metadata_path = run_dir / "metadata.json"
            metadata_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_001",
                        "status": "finished",
                        "stage": "finished",
                        "execution_vina": {"path": str(fake_vina), "version": "unknown-history"},
                        "artifacts": {},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["runs"] = [{"run_id": "run_001", "status": "finished"}]
            project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            recovered = recover_project_state(str(project_dir))
            final = json.loads(metadata_path.read_text(encoding="utf-8"))
            vina_artifact = final["artifacts"]["vina_binary_executed"]

            self.assertTrue(recovered["ok"], recovered)
            self.assertEqual(vina_artifact["sha256"], "")
            self.assertEqual(vina_artifact["verification_status"], "unknown")
            self.assertTrue(vina_artifact["backfilled_unverified"])
            self.assertNotEqual(_sha256(fake_vina), vina_artifact["sha256"])

    def test_new_run_warns_if_vina_binary_changes_during_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            fake_vina, detection, run_id = self._prepare_fake_vina_project(project_dir)
            before_hash = _sha256(fake_vina)

            def fake_run(
                command: list[str],
                cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **_: object,
            ) -> ManagedRunResult:
                out_index = command.index("--out") + 1
                (Path(cwd) / command[out_index]).write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
                Path(stdout_path).write_text("vina stdout\n", encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text("vina stdout\n", encoding="utf-8")
                fake_vina.write_bytes(b"vina-after")
                return ManagedRunResult(pid=None, exit_code=0)

            with (
                patch("dockstart_core.project.vina_adapter.detect", return_value=detection),
                patch("dockstart_core.project.vina_adapter.run_managed", side_effect=fake_run),
            ):
                executed = execute_prepared_vina_run(str(project_dir), run_id)

            metadata = json.loads(
                (project_dir / "runs" / run_id / "metadata.json").read_text(encoding="utf-8"),
            )
            self.assertTrue(executed["ok"], executed)
            self.assertEqual(metadata["artifacts"]["vina_binary_executed"]["sha256"], before_hash)
            self.assertEqual(
                metadata["artifacts"]["vina_binary_observed_after_execution"]["sha256"],
                _sha256(fake_vina),
            )
            self.assertFalse(metadata["vina_binary_integrity"]["match"])
            self.assertTrue(any("发生变化" in warning for warning in metadata["warnings"]))

    def test_analysis_and_report_transactions_preserve_concurrent_metadata_fields(self) -> None:
        vina_log = """mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       -7.4          0          0
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            _, _, run_id = self._prepare_fake_vina_project(project_dir)
            run_dir = project_dir / "runs" / run_id
            (run_dir / "out.pdbqt").write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
            (run_dir / "log.txt").write_text(vina_log, encoding="utf-8")
            (run_dir / "stdout.txt").write_text(vina_log, encoding="utf-8")
            (run_dir / "stderr.txt").write_text("", encoding="utf-8")
            metadata_path = run_dir / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update({"status": "finished", "stage": "finished", "exit_code": 0})
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            update_run_metadata(str(project_dir), run_id, {"before_analysis": "keep"})

            original_export = project_module.export_scores_csv

            def export_with_interleaved_update(*args: object, **kwargs: object) -> dict:
                result = original_export(*args, **kwargs)
                update_run_metadata(str(project_dir), run_id, {"during_analysis": "keep"})
                return result

            with patch("dockstart_core.project.export_scores_csv", side_effect=export_with_interleaved_update):
                analyzed = analyze_vina_run_results(str(project_dir), run_id)

            original_atomic_write = project_module._atomic_write_text
            interleaved = {"done": False}

            def report_write_with_interleaved_update(path: Path, text: str) -> None:
                original_atomic_write(path, text)
                if Path(path).suffix == ".md" and not interleaved["done"]:
                    interleaved["done"] = True
                    update_run_metadata(str(project_dir), run_id, {"during_report": "keep"})

            with patch("dockstart_core.project._atomic_write_text", side_effect=report_write_with_interleaved_update):
                reported = export_markdown_report(str(project_dir), run_id)

            final = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertTrue(analyzed["ok"], analyzed)
            self.assertTrue(reported["ok"], reported)
            self.assertEqual(final["before_analysis"], "keep")
            self.assertEqual(final["during_analysis"], "keep")
            self.assertEqual(final["during_report"], "keep")

    def test_preparation_publication_failure_keeps_previous_output(self) -> None:
        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            Path(command[-1]).write_text("NEW CANDIDATE", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw = project_dir / "raw" / "ligand.sdf"
            raw.write_text("mock sdf", encoding="utf-8")
            output = project_dir / "prepared" / "ligand.pdbqt"
            output.write_text("OLD VERIFIED OUTPUT", encoding="utf-8")
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["ligand"]["raw_file"] = "raw/ligand.sdf"
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            real_replace = os.replace

            def fail_only_final(source: str | bytes | os.PathLike[str] | os.PathLike[bytes], destination: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> None:
                if Path(destination) == output:
                    raise PermissionError("simulated publication failure")
                real_replace(source, destination)

            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_meeko_tools()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
                patch("dockstart_core.persistence.os.replace", side_effect=fail_only_final),
            ):
                result = prepare_ligand_pdbqt(str(project_dir), overwrite=True)

            self.assertFalse(result["ok"])
            self.assertEqual(output.read_text(encoding="utf-8"), "OLD VERIFIED OUTPUT")
            self.assertIn("simulated publication failure", result["error"]["raw_error"])

    def test_real_bundled_ligand_script_accepts_unicode_and_space_path(self) -> None:
        runtime = Path(__file__).resolve().parents[2] / "resources" / "python" / "python.exe"
        sample = (
            Path(__file__).resolve().parents[2]
            / "resources"
            / "examples"
            / "assisted_raw"
            / "raw"
            / "ligand.sdf"
        )
        if not runtime.is_file() or not sample.is_file():
            self.skipTest("本地未装配 Assisted Python runtime 或示例 SDF。")
        with tempfile.TemporaryDirectory(prefix="DockStart 中文 路径 ") as temp_dir:
            root = Path(temp_dir)
            input_path = root / "含 空格 配体.sdf"
            output_path = root / "输出 配体.pdbqt"
            script_path = root / "准备 脚本.py"
            input_path.write_bytes(sample.read_bytes())
            script_path.write_text(_ligand_preparation_script_text(), encoding="utf-8")

            completed = subprocess.run(
                [str(runtime), "-I", "-B", str(script_path), str(input_path), str(output_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=60,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            self.assertTrue(output_path.is_file())
            self.assertGreater(output_path.stat().st_size, 0)

    def test_settings_atomic_write_failure_keeps_previous_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            with patch.dict(os.environ, {SETTINGS_ENV_VAR: str(settings_path)}):
                original = DockStartSettings(tool_paths=ToolPaths(vina="old-vina", python="old-python"))
                save_settings(original)
                old_text = settings_path.read_text(encoding="utf-8")
                real_replace = os.replace

                def fail_settings(source: str | bytes | os.PathLike[str] | os.PathLike[bytes], destination: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> None:
                    if Path(destination) == settings_path:
                        raise PermissionError("simulated settings failure")
                    real_replace(source, destination)

                with patch("dockstart_core.persistence.os.replace", side_effect=fail_settings):
                    with self.assertRaises(PermissionError):
                        save_settings(DockStartSettings(tool_paths=ToolPaths(vina="new-vina", python="new-python")))

                self.assertEqual(settings_path.read_text(encoding="utf-8"), old_text)
                loaded = load_settings()
                self.assertEqual(loaded.tool_paths.vina, "old-vina")
                self.assertEqual(loaded.tool_paths.python, "old-python")


if __name__ == "__main__":
    unittest.main()
