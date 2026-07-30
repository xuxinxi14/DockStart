from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.hydrated_run import (  # noqa: E402
    _copy_verified,
    build_hydrated_markdown_report,
    load_hydrated_results,
)
from dockstart_core.project import HYDRATED_WATERS_MANIFEST_NAME  # noqa: E402
from tests import test_hydrated_project_integration as integration_support  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HydratedFrozenProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = (
            integration_support.HydratedProjectIntegrationTests(
                methodName=(
                    "test_results_and_reports_keep_raw_affinity_and_water_semantics"
                )
            )
        )
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)

    def _finished_run(self) -> tuple[Path, str]:
        project_dir = self.support._create_ready_project()
        prepared = self.support._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        executed = self.support._execute(project_dir, run_id)
        self.assertTrue(executed["ok"], executed)
        return project_dir, run_id

    @staticmethod
    def _metadata(project_dir: Path, run_id: str) -> dict[str, object]:
        return json.loads(
            (
                project_dir / "runs" / run_id / "metadata.json"
            ).read_text(encoding="utf-8")
        )

    @staticmethod
    def _write_metadata(
        project_dir: Path,
        run_id: str,
        metadata: dict[str, object],
    ) -> None:
        (
            project_dir / "runs" / run_id / "metadata.json"
        ).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _sync_record(record: dict[str, object], path: Path) -> None:
        record["size_bytes"] = path.stat().st_size
        record["sha256"] = _sha256(path)

    def test_complete_report_uses_only_verified_run_snapshots(self) -> None:
        project_dir, run_id = self._finished_run()

        results = load_hydrated_results(str(project_dir), run_id)
        report = build_hydrated_markdown_report(str(project_dir), run_id)

        self.assertTrue(results["ok"], results)
        self.assertTrue(report["ok"], report)
        provenance = results["provenance"]
        self.assertEqual(provenance["status"], "verified")
        self.assertEqual(
            provenance["contract_id"],
            "dockstart_hydrated_frozen_provenance_v1",
        )
        for key in (
            "receptor",
            "hydrated_ligand",
            "raw_ligand",
            "added_h_ligand",
            "ligand_manifest",
            "base_maps_manifest",
            "gpf",
            "map:receptor.OA.map",
            "map:receptor.HD.map",
            "map:receptor.W.map",
        ):
            self.assertRegex(
                provenance["verified_files"][key]["sha256"],
                r"^[0-9a-f]{64}$",
            )
        self.assertEqual(
            provenance["verified_files"]["preparation_stderr"]["size_bytes"],
            0,
        )
        self.assertEqual(
            provenance["verified_files"]["autogrid_stderr"]["size_bytes"],
            0,
        )
        text = report["report_text"]
        self.assertIn("完整并已验证", text)
        self.assertIn("2.03 Å", text)
        self.assertIn("±1.0 Å", text)
        self.assertIn("-0.5", text)
        self.assertIn("-0.3", text)
        self.assertIn("hydrated_ad4_best_v1", text)
        self.assertIn("Artifact 哈希索引", text)
        self.assertNotIn(str(self.support.base), text)
        self.assertNotIn(str(self.support.tool_root), text)

    def test_inner_manifest_tamper_fails_after_outer_hashes_are_synced(
        self,
    ) -> None:
        project_dir, run_id = self._finished_run()
        run_dir = project_dir / "runs" / run_id
        ligand_manifest_path = (
            run_dir / "inputs" / "hydrated" / "ligand_manifest.json"
        )
        ligand_manifest = json.loads(
            ligand_manifest_path.read_text(encoding="utf-8")
        )
        ligand_manifest["tools"]["rdkit_version"] = "forged-version"
        ligand_manifest_path.write_text(
            json.dumps(ligand_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        maps_manifest_path = run_dir / "inputs" / "maps" / "manifest.json"
        maps_manifest = json.loads(
            maps_manifest_path.read_text(encoding="utf-8")
        )
        maps_manifest["hydrated_ligand_manifest"]["sha256"] = _sha256(
            ligand_manifest_path
        )
        maps_manifest_path.write_text(
            json.dumps(maps_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        metadata = self._metadata(project_dir, run_id)
        ligand_records = (
            metadata["snapshots"]["hydrated"]["ligand_manifest"],
            metadata["artifacts"]["hydrated_ligand_manifest"],
        )
        for record in ligand_records:
            self._sync_record(record, ligand_manifest_path)
        ligand_hash = _sha256(ligand_manifest_path)
        metadata["artifact_sha256"]["hydrated_ligand_manifest"] = ligand_hash
        metadata["input_sha256"]["hydrated_ligand_manifest"] = ligand_hash

        maps_records = (
            metadata["snapshots"]["ad4_maps"]["manifest"],
            metadata["artifacts"]["hydrated_maps_manifest"],
        )
        for record in maps_records:
            self._sync_record(record, maps_manifest_path)
        maps_hash = _sha256(maps_manifest_path)
        metadata["artifact_sha256"]["hydrated_maps_manifest"] = maps_hash
        metadata["input_sha256"]["maps_manifest"] = maps_hash
        self._write_metadata(project_dir, run_id, metadata)

        result = load_hydrated_results(str(project_dir), run_id)

        self.assertFalse(result["ok"], result)
        self.assertEqual(
            result["error"]["code"],
            "HYDRATED_PROVENANCE_PREPARATION_TOOL_SEMANTICS_MISMATCH",
        )

    def test_waters_parameter_tamper_fails_after_outer_hash_is_synced(
        self,
    ) -> None:
        project_dir, run_id = self._finished_run()
        manifest_path = (
            project_dir
            / "runs"
            / run_id
            / HYDRATED_WATERS_MANIFEST_NAME
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["parameters"]["strong_threshold"] = -0.6
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        metadata = self._metadata(project_dir, run_id)
        manifest_hash = _sha256(manifest_path)
        metadata["hydrated_postprocess"]["manifest_sha256"] = manifest_hash
        self._sync_record(
            metadata["artifacts"]["hydrated_waters_manifest"],
            manifest_path,
        )
        metadata["artifact_sha256"]["hydrated_waters_manifest"] = manifest_hash
        self._write_metadata(project_dir, run_id, metadata)

        result = load_hydrated_results(str(project_dir), run_id)

        self.assertFalse(result["ok"], result)
        self.assertEqual(
            result["error"]["code"],
            "HYDRATED_PROVENANCE_POSTPROCESS_CONTRACT_MISMATCH",
        )

    def test_map_manifest_contract_failure_reports_compared_fields(
        self,
    ) -> None:
        project_dir, run_id = self._finished_run()
        base_manifest_path = (
            project_dir
            / "runs"
            / run_id
            / "inputs"
            / "maps"
            / "base_manifest.json"
        )
        base_manifest = json.loads(
            base_manifest_path.read_text(encoding="utf-8")
        )
        base_manifest["status"] = "forged-status"
        base_manifest_path.write_text(
            json.dumps(base_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        metadata = self._metadata(project_dir, run_id)
        for record in (
            metadata["snapshots"]["ad4_maps"]["base_manifest"],
            metadata["artifacts"]["hydrated_base_maps_manifest"],
        ):
            self._sync_record(record, base_manifest_path)
        base_hash = _sha256(base_manifest_path)
        metadata["artifact_sha256"]["hydrated_base_maps_manifest"] = base_hash
        metadata["input_sha256"]["base_maps_manifest"] = base_hash
        self._write_metadata(project_dir, run_id, metadata)

        result = load_hydrated_results(str(project_dir), run_id)

        self.assertFalse(result["ok"], result)
        self.assertEqual(
            result["error"]["code"],
            "HYDRATED_PROVENANCE_MAP_MANIFEST_CONTRACT_MISMATCH",
        )
        diagnostic = json.loads(result["error"]["raw_error"])
        self.assertEqual(
            diagnostic["base_manifest"]["status"],
            "forged-status",
        )
        self.assertEqual(
            diagnostic["hydrated_manifest"]["files"],
            diagnostic["frozen_map_files"],
        )
        self.assertEqual(
            diagnostic["expected_base_files"],
            diagnostic["base_manifest"]["required_files"],
        )
        self.assertEqual(
            diagnostic["base_manifest"]["optional_files"],
            ["receptor.maps.xyz"],
        )

    def test_optional_maps_xyz_forgery_fails_after_outer_hashes_are_synced(
        self,
    ) -> None:
        for field in ("relative_path", "size_bytes", "sha256"):
            with self.subTest(field=field):
                project_dir, run_id = self._finished_run()
                run_dir = project_dir / "runs" / run_id
                base_manifest_path = (
                    run_dir / "inputs" / "maps" / "base_manifest.json"
                )
                maps_manifest_path = (
                    run_dir / "inputs" / "maps" / "manifest.json"
                )
                base_manifest = json.loads(
                    base_manifest_path.read_text(encoding="utf-8")
                )
                xyz_record = next(
                    record
                    for record in base_manifest["maps"]["files"]
                    if record["name"] == "receptor.maps.xyz"
                )
                if field == "relative_path":
                    xyz_record[field] = (
                        "maps/forged/receptor.maps.xyz"
                    )
                elif field == "size_bytes":
                    xyz_record[field] = int(xyz_record[field]) + 1
                else:
                    xyz_record[field] = "b" * 64
                base_manifest_path.write_text(
                    json.dumps(
                        base_manifest,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )

                maps_manifest = json.loads(
                    maps_manifest_path.read_text(encoding="utf-8")
                )
                self._sync_record(
                    maps_manifest["base_maps_manifest"],
                    base_manifest_path,
                )
                maps_manifest_path.write_text(
                    json.dumps(
                        maps_manifest,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )

                metadata = self._metadata(project_dir, run_id)
                for record in (
                    metadata["snapshots"]["ad4_maps"][
                        "base_manifest"
                    ],
                    metadata["artifacts"][
                        "hydrated_base_maps_manifest"
                    ],
                ):
                    self._sync_record(record, base_manifest_path)
                base_hash = _sha256(base_manifest_path)
                metadata["artifact_sha256"][
                    "hydrated_base_maps_manifest"
                ] = base_hash
                metadata["input_sha256"][
                    "base_maps_manifest"
                ] = base_hash

                for record in (
                    metadata["snapshots"]["ad4_maps"]["manifest"],
                    metadata["artifacts"]["hydrated_maps_manifest"],
                ):
                    self._sync_record(record, maps_manifest_path)
                maps_hash = _sha256(maps_manifest_path)
                metadata["artifact_sha256"][
                    "hydrated_maps_manifest"
                ] = maps_hash
                metadata["input_sha256"]["maps_manifest"] = maps_hash
                self._write_metadata(project_dir, run_id, metadata)

                result = load_hydrated_results(
                    str(project_dir),
                    run_id,
                )

                self.assertFalse(result["ok"], result)
                self.assertEqual(
                    result["error"]["code"],
                    (
                        "HYDRATED_PROVENANCE_"
                        "MAP_MANIFEST_CONTRACT_MISMATCH"
                    ),
                )
                self.assertNotEqual(
                    (result.get("provenance") or {}).get("status"),
                    "verified",
                )

    def test_optional_maps_xyz_audit_path_forgery_fails_closed(
        self,
    ) -> None:
        project_dir, run_id = self._finished_run()
        maps_manifest_path = (
            project_dir
            / "runs"
            / run_id
            / "inputs"
            / "maps"
            / "manifest.json"
        )
        maps_manifest = json.loads(
            maps_manifest_path.read_text(encoding="utf-8")
        )
        xyz_audit = next(
            record
            for record in maps_manifest["audit_files"]
            if record["name"] == "receptor.maps.xyz"
        )
        xyz_audit["relative_path"] = "maps/forged/receptor.maps.xyz"
        maps_manifest_path.write_text(
            json.dumps(maps_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        metadata = self._metadata(project_dir, run_id)
        for record in (
            metadata["snapshots"]["ad4_maps"]["manifest"],
            metadata["artifacts"]["hydrated_maps_manifest"],
        ):
            self._sync_record(record, maps_manifest_path)
        maps_hash = _sha256(maps_manifest_path)
        metadata["artifact_sha256"]["hydrated_maps_manifest"] = maps_hash
        metadata["input_sha256"]["maps_manifest"] = maps_hash
        self._write_metadata(project_dir, run_id, metadata)

        result = load_hydrated_results(str(project_dir), run_id)

        self.assertFalse(result["ok"], result)
        self.assertEqual(
            result["error"]["code"],
            "HYDRATED_PROVENANCE_MAP_MANIFEST_CONTRACT_MISMATCH",
        )
        self.assertNotEqual(
            (result.get("provenance") or {}).get("status"),
            "verified",
        )

    def test_legacy_run_is_explicit_partial_read_only(self) -> None:
        project_dir, run_id = self._finished_run()
        metadata = self._metadata(project_dir, run_id)
        metadata["hydrated"].pop("provenance_schema_version")
        metadata["hydrated"].pop("provenance_contract_id")
        snapshot_hydrated = metadata["snapshots"]["hydrated"]
        snapshot_hydrated.pop("provenance_schema_version")
        snapshot_hydrated.pop("provenance_contract_id")
        for key in (
            "raw_ligand",
            "added_h_ligand",
            "preparation_script",
            "preparation_stdout",
            "preparation_stderr",
        ):
            snapshot_hydrated.pop(key)
        metadata["snapshots"].pop("command_preview")
        for key in (
            "base_manifest",
            "gpf",
            "autogrid_stdout",
            "autogrid_stderr",
            "autogrid_log",
        ):
            metadata["snapshots"]["ad4_maps"].pop(key)
        new_artifact_keys = (
            "hydrated_raw_ligand",
            "hydrated_added_h_ligand",
            "hydrated_preparation_script",
            "hydrated_preparation_stdout",
            "hydrated_preparation_stderr",
            "hydrated_base_maps_manifest",
            "hydrated_gpf",
            "hydrated_autogrid_stdout",
            "hydrated_autogrid_stderr",
            "hydrated_autogrid_log",
            "hydrated_command_preview",
        )
        for key in new_artifact_keys:
            metadata["artifacts"].pop(key)
            metadata["artifact_sha256"].pop(key)
        self._write_metadata(project_dir, run_id, metadata)

        results = load_hydrated_results(str(project_dir), run_id)
        report = build_hydrated_markdown_report(str(project_dir), run_id)

        self.assertTrue(results["ok"], results)
        self.assertEqual(results["provenance"]["status"], "legacy_partial")
        self.assertTrue(results["provenance"]["read_only"])
        self.assertTrue(results["provenance"]["missing_evidence"])
        self.assertTrue(report["ok"], report)
        self.assertIn("旧版/部分溯源，只读", report["report_text"])
        self.assertIn("不会从当前 project.json", report["report_text"])
        self.assertNotIn("完整并已验证", report["report_text"])
        self.assertNotIn(str(self.support.base), report["report_text"])

    def test_copy_verified_accepts_zero_byte_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = hashlib.sha256(b"").hexdigest()
            for name in ("stdout.txt", "stderr.txt"):
                source = root / f"source_{name}"
                destination = root / f"frozen_{name}"
                source.write_bytes(b"")

                snapshot = _copy_verified(
                    source,
                    destination,
                    expected_sha256=expected,
                )

                self.assertEqual(snapshot["size_bytes"], 0)
                self.assertEqual(snapshot["sha256"], expected)
                self.assertEqual(destination.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
