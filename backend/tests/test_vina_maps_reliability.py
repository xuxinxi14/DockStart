from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.vina_maps import (  # noqa: E402
    activate_map_set,
    generate_maps,
    import_maps,
    validate_active_maps,
)
from tests import test_vina_maps as maps_test_support  # noqa: E402


class VinaMapsReliabilityTests(unittest.TestCase):
    def _setup(
        self,
        base_dir: str,
        name: str = "maps_reliability",
    ) -> tuple[
        Path,
        Path,
        maps_test_support.VinaMapsWorkflowTests,
    ]:
        helper = maps_test_support.VinaMapsWorkflowTests(methodName="runTest")
        project_dir = helper._create_project(base_dir, name)
        executable = Path(base_dir) / "带 空格的 vina.exe"
        executable.write_bytes(b"mock Vina 1.2.7 reliability")
        return project_dir, executable, helper

    @staticmethod
    def _project_payload(project_dir: Path) -> dict[str, object]:
        return json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8")
        )

    @classmethod
    def _active_map_set_id(cls, project_dir: Path) -> str:
        payload = cls._project_payload(project_dir)
        record = payload.get("vina_maps")
        return (
            str(record.get("map_set_id") or "")
            if isinstance(record, dict)
            else ""
        )

    def _assert_no_staging(self, project_dir: Path) -> None:
        self.assertEqual(
            list((project_dir / "maps").glob(".vina-maps-staging-*")),
            [],
        )

    def test_concurrent_generation_is_serialized_and_publishes_unique_ready_sets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = [
                        executor.submit(
                            generate_maps,
                            str(project_dir),
                            {"activate": False},
                            runner=helper._runner,
                        )
                        for _ in range(4)
                    ]
                    results = [future.result(timeout=20) for future in futures]

            self.assertTrue(all(item["ok"] for item in results), results)
            self.assertEqual(
                sorted(item["map_set_id"] for item in results),
                ["vina_001", "vina_002", "vina_003", "vina_004"],
            )
            for result in results:
                manifest = (
                    project_dir / result["manifest_file"]
                )
                self.assertTrue(manifest.is_file())
                self.assertEqual(
                    json.loads(manifest.read_text(encoding="utf-8"))["status"],
                    "ready",
                )
            self._assert_no_staging(project_dir)

    def test_concurrent_activation_keeps_project_pointer_internally_consistent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                generated = [
                    generate_maps(
                        str(project_dir),
                        {"activate": False},
                        runner=helper._runner,
                    )
                    for _ in range(2)
                ]
                self.assertTrue(all(item["ok"] for item in generated), generated)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            activate_map_set,
                            str(project_dir),
                            item["map_set_id"],
                            runner=helper._runner,
                        )
                        for item in generated
                    ]
                    activated = [
                        future.result(timeout=20) for future in futures
                    ]
                status = validate_active_maps(
                    str(project_dir),
                    runner=helper._runner,
                )

            self.assertTrue(all(item["ok"] for item in activated), activated)
            payload = self._project_payload(project_dir)
            record = payload["vina_maps"]
            protocol = payload["docking_protocol"]
            self.assertIn(
                record["map_set_id"],
                {item["map_set_id"] for item in generated},
            )
            self.assertEqual(
                record["active_manifest"],
                f"maps/{record['map_set_id']}/manifest.json",
            )
            self.assertEqual(
                protocol["active_map_set_id"],
                record["map_set_id"],
            )
            self.assertTrue(status["ok"], status)
            self.assertEqual(status["map_set_id"], record["map_set_id"])

    def test_generation_failure_and_interrupt_do_not_publish_or_replace_active(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)

            def failed_runner(
                command: list[str],
                cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
            ) -> dict[str, object]:
                helper._write_maps(command, Path(cwd))
                Path(stdout_path).write_text("", encoding="utf-8")
                Path(stderr_path).write_text("forced failure", encoding="utf-8")
                Path(log_path).write_text("", encoding="utf-8")
                return {
                    "ok": False,
                    "command": command,
                    "pid": 91,
                    "exit_code": 1,
                    "error": "forced failure",
                }

            def interrupted_runner(
                command: list[str],
                cwd: str | Path,
                _stdout_path: str | Path,
                _stderr_path: str | Path,
                _log_path: str | Path,
            ) -> dict[str, object]:
                helper._write_maps(command, Path(cwd))
                raise KeyboardInterrupt("forced interrupt")

            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                first = generate_maps(str(project_dir), runner=helper._runner)
                self.assertTrue(first["ok"], first)
                failed = generate_maps(str(project_dir), runner=failed_runner)
                with self.assertRaises(KeyboardInterrupt):
                    generate_maps(str(project_dir), runner=interrupted_runner)
                status = validate_active_maps(
                    str(project_dir),
                    runner=helper._runner,
                )

            self.assertFalse(failed["ok"])
            self.assertEqual(
                failed["error"]["code"],
                "VINA_MAPS_GENERATION_FAILED",
            )
            self.assertEqual(self._active_map_set_id(project_dir), "vina_001")
            self.assertFalse((project_dir / "maps" / "vina_002").exists())
            self._assert_no_staging(project_dir)
            self.assertTrue(status["ok"], status)

    def test_import_copy_failure_does_not_publish_or_replace_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)
            real_copyfile = shutil.copyfile
            copied_maps = 0

            def flaky_copyfile(
                source: str | Path,
                target: str | Path,
            ) -> str:
                nonlocal copied_maps
                if str(source).endswith(".map"):
                    copied_maps += 1
                    if copied_maps == 2:
                        raise OSError("simulated disk failure")
                return str(real_copyfile(source, target))

            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                first = generate_maps(str(project_dir), runner=helper._runner)
                self.assertTrue(first["ok"], first)
                with patch(
                    "dockstart_core.vina_maps.shutil.copyfile",
                    side_effect=flaky_copyfile,
                ):
                    imported = import_maps(
                        str(project_dir),
                        str(project_dir / first["manifest_file"]),
                        runner=helper._runner,
                    )
                status = validate_active_maps(
                    str(project_dir),
                    runner=helper._runner,
                )

            self.assertFalse(imported["ok"])
            self.assertEqual(
                imported["error"]["code"],
                "VINA_MAPS_IMPORT_FAILED",
            )
            self.assertEqual(self._active_map_set_id(project_dir), "vina_001")
            self.assertFalse((project_dir / "maps" / "vina_002").exists())
            self._assert_no_staging(project_dir)
            self.assertTrue(status["ok"], status)

    def test_activation_project_save_failure_rolls_back_new_publication(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)
            save_conflict = {
                "ok": False,
                "project": None,
                "error": {
                    "code": "PROJECT_SAVE_CONFLICT",
                    "message": "simulated project revision conflict",
                    "raw_error": "expected revision changed",
                    "suggestion": "reload",
                },
            }
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                active = generate_maps(str(project_dir), runner=helper._runner)
                self.assertTrue(active["ok"], active)
                with patch(
                    "dockstart_core.vina_maps.save_project",
                    return_value=save_conflict,
                ):
                    rejected = generate_maps(
                        str(project_dir),
                        runner=helper._runner,
                    )
                status = validate_active_maps(
                    str(project_dir),
                    runner=helper._runner,
                )

            self.assertFalse(rejected["ok"])
            self.assertEqual(
                rejected["error"]["code"],
                "PROJECT_SAVE_CONFLICT",
            )
            self.assertEqual(rejected["map_set_id"], "vina_002")
            self.assertEqual(
                rejected["publication_rollback"],
                {
                    "attempted": True,
                    "removed_uncommitted_set": True,
                },
            )
            self.assertFalse((project_dir / "maps" / "vina_002").exists())
            self.assertEqual(
                self._active_map_set_id(project_dir),
                active["map_set_id"],
            )
            self._assert_no_staging(project_dir)
            self.assertTrue(status["ok"], status)

    def test_probe_exception_preserves_old_active_and_retry_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)

            def probe_raises(
                _command: list[str],
                _cwd: str | Path,
                _stdout_path: str | Path,
                _stderr_path: str | Path,
                _log_path: str | Path,
            ) -> dict[str, object]:
                raise RuntimeError("simulated probe adapter exception")

            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                active = generate_maps(str(project_dir), runner=helper._runner)
                candidate = generate_maps(
                    str(project_dir),
                    {"activate": False},
                    runner=helper._runner,
                )
                self.assertTrue(active["ok"] and candidate["ok"])
                failed = activate_map_set(
                    str(project_dir),
                    candidate["map_set_id"],
                    runner=probe_raises,
                )
                active_after_failure = self._active_map_set_id(project_dir)
                recovered = activate_map_set(
                    str(project_dir),
                    candidate["map_set_id"],
                    runner=helper._runner,
                )

            self.assertFalse(failed["ok"])
            self.assertEqual(
                failed["error"]["code"],
                "VINA_MAPS_LIGAND_PROBE_FAILED",
            )
            self.assertIn(
                "simulated probe adapter exception",
                failed["error"]["raw_error"],
            )
            self.assertEqual(active_after_failure, active["map_set_id"])
            self.assertTrue(recovered["ok"], recovered)
            self.assertEqual(
                self._active_map_set_id(project_dir),
                candidate["map_set_id"],
            )

    def test_probe_interrupt_preserves_old_active_and_removes_partial_probe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)

            def interrupted_probe(
                _command: list[str],
                _cwd: str | Path,
                _stdout_path: str | Path,
                _stderr_path: str | Path,
                _log_path: str | Path,
            ) -> dict[str, object]:
                raise KeyboardInterrupt("simulated probe interrupt")

            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                active = generate_maps(str(project_dir), runner=helper._runner)
                candidate = generate_maps(
                    str(project_dir),
                    {"activate": False},
                    runner=helper._runner,
                )
                self.assertTrue(active["ok"] and candidate["ok"])
                with self.assertRaises(KeyboardInterrupt):
                    activate_map_set(
                        str(project_dir),
                        candidate["map_set_id"],
                        runner=interrupted_probe,
                    )
                active_after_interrupt = self._active_map_set_id(project_dir)
                recovered = activate_map_set(
                    str(project_dir),
                    candidate["map_set_id"],
                    runner=helper._runner,
                )

            probes_dir = (
                project_dir
                / "maps"
                / candidate["map_set_id"]
                / "probes"
            )
            self.assertEqual(
                sorted(path.name for path in probes_dir.glob("probe_*")),
                ["probe_001"],
            )
            self.assertEqual(active_after_interrupt, active["map_set_id"])
            self.assertTrue(recovered["ok"], recovered)
            self.assertEqual(
                self._active_map_set_id(project_dir),
                candidate["map_set_id"],
            )

    def test_map_or_manifest_replacement_during_probe_is_fail_closed(self) -> None:
        for mutation in ("map", "manifest"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, executable, helper = self._setup(
                    temp_dir,
                    f"maps_toctou_{mutation}",
                )
                with patch(
                    "dockstart_core.vina_maps.vina_adapter.detect",
                    return_value=helper._vina_result(executable),
                ):
                    active = generate_maps(
                        str(project_dir),
                        runner=helper._runner,
                    )
                    candidate = generate_maps(
                        str(project_dir),
                        {"activate": False},
                        runner=helper._runner,
                    )
                    self.assertTrue(active["ok"] and candidate["ok"])
                    set_dir = (
                        project_dir / "maps" / candidate["map_set_id"]
                    )

                    def mutating_probe(
                        command: list[str],
                        cwd: str | Path,
                        stdout_path: str | Path,
                        stderr_path: str | Path,
                        log_path: str | Path,
                    ) -> dict[str, object]:
                        result = helper._runner(
                            command,
                            cwd,
                            stdout_path,
                            stderr_path,
                            log_path,
                        )
                        if mutation == "map":
                            map_path = (
                                set_dir
                                / candidate["manifest"]["maps"]["files"][0]["name"]
                            )
                            map_path.write_bytes(
                                map_path.read_bytes() + b"0.0\n"
                            )
                        else:
                            manifest_path = set_dir / "manifest.json"
                            manifest = json.loads(
                                manifest_path.read_text(encoding="utf-8")
                            )
                            manifest["provenance"]["probe_mutation"] = True
                            manifest_path.write_text(
                                json.dumps(
                                    manifest,
                                    ensure_ascii=False,
                                    indent=2,
                                )
                                + "\n",
                                encoding="utf-8",
                            )
                        return result

                    rejected = activate_map_set(
                        str(project_dir),
                        candidate["map_set_id"],
                        runner=mutating_probe,
                    )

                self.assertFalse(rejected["ok"])
                self.assertEqual(
                    rejected["error"]["code"],
                    (
                        "VINA_MAPS_LIGAND_PROBE_FAILED"
                        if mutation == "map"
                        else "VINA_MAPS_ACTIVATION_INPUT_CHANGED"
                    ),
                )
                self.assertEqual(
                    self._active_map_set_id(project_dir),
                    active["map_set_id"],
                )

    def test_long_chinese_space_paths_support_generate_manifest_import_and_probe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            long_base = (
                Path(temp_dir)
                / "包含 中文与 空格的预计算网格目录"
                / "第二层 很长的科研项目路径"
                / "第三层 maps 复现实验"
            )
            long_base.mkdir(parents=True)
            source, executable, helper = self._setup(
                str(long_base),
                "source_project",
            )
            target = helper._create_project(
                str(long_base),
                "target_project",
            )
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                generated = generate_maps(
                    str(source),
                    {"activate": False},
                    runner=helper._runner,
                )
                self.assertTrue(generated["ok"], generated)
                imported = import_maps(
                    str(target),
                    str(source / generated["manifest_file"]),
                    runner=helper._runner,
                )
                status = validate_active_maps(
                    str(target),
                    runner=helper._runner,
                )

            self.assertTrue(imported["ok"], imported)
            self.assertTrue(status["ok"], status)
            self.assertIn("中文", str(target))
            self.assertIn(" ", str(target))

    def test_incomplete_manifest_is_read_only_and_requires_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                generated = generate_maps(
                    str(project_dir),
                    {"activate": False},
                    runner=helper._runner,
                )
                self.assertTrue(generated["ok"], generated)
                manifest_path = project_dir / generated["manifest_file"]
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                manifest.pop("semantics")
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                before = manifest_path.read_bytes()
                rejected = activate_map_set(
                    str(project_dir),
                    generated["map_set_id"],
                    runner=helper._runner,
                )

            self.assertFalse(rejected["ok"])
            self.assertEqual(
                rejected["error"]["code"],
                "VINA_MAPS_MANIFEST_REBUILD_REQUIRED",
            )
            self.assertEqual(manifest_path.read_bytes(), before)
            self.assertEqual(self._active_map_set_id(project_dir), "")

    def test_unsupported_manifest_schema_is_read_only_and_requires_rebuild(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                generated = generate_maps(
                    str(project_dir),
                    {"activate": False},
                    runner=helper._runner,
                )
                self.assertTrue(generated["ok"], generated)
                manifest_path = project_dir / generated["manifest_file"]
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                manifest["schema_version"] = 0
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                before = manifest_path.read_bytes()
                rejected = activate_map_set(
                    str(project_dir),
                    generated["map_set_id"],
                    runner=helper._runner,
                )

            self.assertFalse(rejected["ok"])
            self.assertEqual(
                rejected["error"]["code"],
                "VINA_MAPS_MANIFEST_REBUILD_REQUIRED",
            )
            self.assertEqual(manifest_path.read_bytes(), before)
            self.assertEqual(self._active_map_set_id(project_dir), "")

    def test_legacy_project_record_without_manifest_is_explicitly_rejected_read_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._setup(temp_dir)
            project_file = project_dir / "project.json"
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            payload["vina_maps"] = {
                "map_set_id": "vina_001",
                "status": "ready",
            }
            payload["docking_protocol"] = {
                "engine": "vina",
                "protocol_id": "vina_maps",
                "grid_source": "precomputed_maps",
                "active_map_set_id": "vina_001",
            }
            project_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = project_file.read_bytes()
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                status = validate_active_maps(
                    str(project_dir),
                    runner=helper._runner,
                )

            self.assertFalse(status["ok"])
            self.assertEqual(
                status["error"]["code"],
                "VINA_MAPS_RECORD_REBUILD_REQUIRED",
            )
            self.assertEqual(project_file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
