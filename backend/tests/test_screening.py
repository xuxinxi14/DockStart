from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import dockstart_core.screening as screening  # noqa: E402
from dockstart_core.autogrid import generate_maps  # noqa: E402
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    create_project,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    update_box_params,
)
from dockstart_core.screening import (  # noqa: E402
    archive_screening,
    compare_screening_archives,
    create_screening,
    export_screening_archive_zip,
    export_screening_markdown_report,
    generate_screening_result_sdf,
    get_screening_archive,
    get_screening_status,
    list_screening_archives,
    main,
    request_screening_cancel,
    retry_screening_preparation,
    resume_screening,
    run_screening,
    stage_screening_inputs,
)
from dockstart_core.viewer import (  # noqa: E402
    load_archived_screening_pose_for_viewer,
    load_screening_pose_for_viewer,
    main as viewer_main,
)


BOX = {
    "center_x": 1,
    "center_y": 2,
    "center_z": 3,
    "size_x": 20,
    "size_y": 20,
    "size_z": 20,
}
VINA = {
    "scoring": "vina",
    "exhaustiveness": 8,
    "num_modes": 9,
    "energy_range": 3,
    "cpu": 2,
    "seed": 123,
}
SUPPORTED_ADVANCED_CAPABILITIES = {
    "features": {
        "no_refine": {
            "status": "supported",
            "supported": True,
            "message": "supported",
        },
        "force_even_voxels": {
            "status": "supported",
            "supported": True,
            "message": "supported",
        },
    }
}


def _pdbqt(atom_name: str = "C") -> str:
    return (
        f"ATOM      1  {atom_name:<3} LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C\n"
        "TORSDOF 0\n"
    )


def _resource_limits_hash(value: dict) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _screening_preparation_tools() -> dict:
    return {
        "ok": True,
        "tools": {
            "python": {
                "status": "ok",
                # A regular, readable file keeps the tool-snapshot integrity
                # test independent of Windows Store Python reparse aliases.
                "path": str(Path(__file__).resolve()),
                "version": ".".join(
                    str(value) for value in sys.version_info[:3]
                ),
                "source": "current_environment",
            },
            "rdkit": {
                "status": "ok",
                "version": "test-rdkit",
                "source": "current_environment",
            },
            "meeko": {
                "status": "ok",
                "version": "test-meeko",
                "source": "current_environment",
            },
        },
    }


def _sdf_record_bytes(path: Path, index: int) -> bytes:
    records: list[bytes] = []
    current: list[bytes] = []
    for line in path.read_bytes().splitlines(keepends=True):
        current.append(line)
        if line.strip() == b"$$$$":
            records.append(b"".join(current))
            current = []
    if current and b"".join(current).strip():
        records.append(b"".join(current))
    return records[index - 1]


def _fake_chemical_facts(
    raw_record: bytes,
    *,
    canonical_sha256: str | None = None,
    has_macrocycle: bool = False,
) -> dict:
    atom_count = 8 if has_macrocycle else 2
    bond_count = 8 if has_macrocycle else 1
    return {
        "schema_version": 1,
        "status": "verified",
        "source": "frozen_raw_topology",
        "calculation_profile": (
            "rdkit_source_formal_charge_heavy_atoms_"
            "strict_rotatable_bonds_v1"
        ),
        "source_topology_sha256": hashlib.sha256(raw_record).hexdigest(),
        "rdkit_version": "test-rdkit",
        "formal_charge": 0,
        "heavy_atom_count": atom_count,
        "rotatable_bond_count": 0,
        "fragment_count": 1,
        "max_ring_size": 8 if has_macrocycle else 0,
        "has_macrocycle": has_macrocycle,
        "canonical_topology_sha256": (
            canonical_sha256
            or hashlib.sha256(b"canonical:" + raw_record).hexdigest()
        ),
        "canonical_atom_count": atom_count,
        "canonical_bond_count": bond_count,
    }


def _fake_worker_evidence() -> dict:
    return {
        "schema_version": 2,
        "preparation_profile": "dockstart_screening_rdkit_meeko_fail_closed_v2",
        "hydrogen_policy": "rdkit_add_hs_preserve_source_indices_v1",
        "toolchain": {
            "python_version": "test-python",
            "rdkit_version": "test-rdkit",
            "meeko_version": "test-meeko",
        },
        "worker_script_sha256": "a" * 64,
        "worker_manifest_sha256": "b" * 64,
        "worker_manifest_size_bytes": 1,
    }


def _fake_worker_identity(
    source: Path,
    index: int,
    *,
    include_facts: bool = True,
    canonical_sha256: str | None = None,
    has_macrocycle: bool = False,
) -> dict:
    raw_record = _sdf_record_bytes(source, index)
    result = {
        "source_record_sha256": hashlib.sha256(raw_record).hexdigest(),
        "source_record_size_bytes": len(raw_record),
        "_worker_evidence": _fake_worker_evidence(),
    }
    if include_facts:
        result["chemical_facts"] = _fake_chemical_facts(
            raw_record,
            canonical_sha256=canonical_sha256,
            has_macrocycle=has_macrocycle,
        )
    return result


def _fake_library_worker(specifications: list[dict]):
    def worker(
        root,
        original,
        python_path,
        *,
        max_records,
        expected_sha256,
        expected_size_bytes,
        record_dir=None,
    ):
        staging_root = root / "screening" / "staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(
            prefix=".test-library-",
            dir=staging_root,
        )
        temporary_root = Path(temporary.name)
        records: list[dict] = []
        for specification in specifications:
            index = int(specification["index"])
            status = str(specification.get("status") or "ready")
            record = {
                "status": status,
                "source_record_index": index,
                "source_record_name": str(
                    specification.get("name") or f"Record {index}"
                ),
                **_fake_worker_identity(
                    original,
                    index,
                    include_facts=bool(
                        specification.get("include_facts", True)
                    ),
                    canonical_sha256=specification.get(
                        "canonical_sha256"
                    ),
                    has_macrocycle=bool(
                        specification.get("has_macrocycle", False)
                    ),
                ),
            }
            if status == "ready":
                output = temporary_root / f"record_{index:06d}.pdbqt"
                output.write_text(
                    _pdbqt(str(specification.get("atom") or "C")),
                    encoding="utf-8",
                )
                record["_pdbqt_path"] = output
            else:
                record["error"] = dict(
                    specification.get("error")
                    or {
                        "code": "LIGAND_PREPARATION_FAILED",
                        "message": "测试准备失败。",
                    }
                )
            records.append(record)
        return temporary, records

    return worker


def _successful_runner(
    calls: list[tuple[str, int]],
    *,
    fail_first_item_once: bool = False,
    score_offset: float = 0.0,
):
    def runner(**kwargs):
        item_id = kwargs["item"]["item_id"]
        attempt = kwargs["attempt"]
        calls.append((item_id, attempt))
        if fail_first_item_once and item_id == "ligand_0001" and attempt == 1:
            kwargs["stderr_path"].write_text("temporary error", encoding="utf-8")
            return {"exit_code": 2, "error": "temporary error"}
        affinity = -8.0 - float(kwargs["item"]["order"]) + score_offset
        kwargs["output_path"].write_text(_pdbqt(), encoding="utf-8")
        kwargs["log_path"].write_text(
            "mode |   affinity | dist from best mode\n"
            "     | (kcal/mol) | rmsd l.b.| rmsd u.b.\n"
            "-----+------------+----------+----------\n"
            f"   1      {affinity:.2f}      0.000      0.000\n",
            encoding="utf-8",
        )
        return {"exit_code": 0, "pid": 101}

    return runner


class ScreeningWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "prepared").mkdir()
        (self.root / "prepared" / "receptor.pdbqt").write_text(_pdbqt("N"), encoding="utf-8")
        (self.root / "prepared" / "zeta.pdbqt").write_text(_pdbqt("C"), encoding="utf-8")
        (self.root / "prepared" / "alpha.pdbqt").write_text(_pdbqt("O"), encoding="utf-8")
        self.vina = self.root / "vina.exe"
        self.vina.write_bytes(b"placeholder")
        self.project_json = self.root / "project.json"
        self.project_json.write_text('{"schema_version": 99, "sentinel": true}\n', encoding="utf-8")
        self.project_before = self.project_json.read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self, **overrides):
        mock_detection = bool(overrides.pop("mock_detection", True))
        detection_capabilities = overrides.pop("detection_capabilities", {})
        arguments = {
            "project_dir": str(self.root),
            "receptor_file": "prepared/receptor.pdbqt",
            "ligand_files": ["prepared/zeta.pdbqt", "prepared/alpha.pdbqt"],
            "vina_path": str(self.vina),
            "box": BOX,
            "vina": VINA,
            "max_retries": 1,
            "top_n": 2,
        }
        arguments.update(overrides)
        if not mock_detection:
            return create_screening(**arguments)
        detection = SimpleNamespace(
            status="ok",
            path=str(self.vina),
            version="1.2.7",
            source="configured",
            message="ok",
            raw_error="",
            capabilities=detection_capabilities,
        )
        with patch("dockstart_core.screening.vina_adapter.detect", return_value=detection):
            return create_screening(**arguments)

    def create_completed_archive(self, **overrides):
        runner = overrides.pop("runner", _successful_runner([]))
        created = self.create(**overrides)
        self.assertTrue(created["ok"], created)
        finished = run_screening(str(self.root), runner=runner)
        self.assertTrue(finished["ok"], finished)
        archived = archive_screening(str(self.root))
        self.assertTrue(archived["ok"], archived)
        return archived

    def test_standard_ad4_batch_freezes_maps_runs_and_archives(self) -> None:
        parent = self.root / "ad4_parent"
        parent.mkdir()
        created_project = create_project("ad4_batch", str(parent))
        self.assertTrue(created_project["ok"], created_project)
        project = Path(created_project["project_dir"])
        receptor_source = parent / "ad4_receptor.pdbqt"
        ligand_source = parent / "ad4_ligand.pdbqt"
        receptor_source.write_text(_pdbqt("N"), encoding="utf-8")
        ligand_source.write_text(_pdbqt("C"), encoding="utf-8")
        self.assertTrue(
            import_receptor_pdbqt(str(project), str(receptor_source))["ok"]
        )
        self.assertTrue(
            import_ligand_pdbqt(str(project), str(ligand_source))["ok"]
        )
        self.assertTrue(update_box_params(str(project), BOX)["ok"])
        second_ligand = project / "prepared" / "ligand_two.pdbqt"
        second_ligand.write_text(_pdbqt("O"), encoding="utf-8")

        autogrid = parent / "autogrid4.exe"
        autogrid.write_bytes(b"mock autogrid")

        def fake_autogrid(
            _executable: str,
            gpf_file: str,
            log_file: str,
            working_directory: str | Path,
            **_kwargs: object,
        ) -> dict:
            root = Path(working_directory)
            gpf_text = (root / gpf_file).read_text(encoding="utf-8")
            ligand_types = next(
                line.split()[1:]
                for line in gpf_text.splitlines()
                if line.startswith("ligand_types ")
            )
            for name in (
                "receptor.maps.fld",
                *(f"receptor.{atom_type}.map" for atom_type in ligand_types),
                "receptor.e.map",
                "receptor.d.map",
            ):
                (root / name).write_text(f"mock {name}\n", encoding="utf-8")
            (root / log_file).write_text(
                "Successful Completion\n",
                encoding="utf-8",
            )
            return {
                "ok": True,
                "command": ["autogrid4", "-p", gpf_file, "-l", log_file],
                "exit_code": 0,
                "stdout": "AutoGrid complete\n",
                "stderr": "",
                "error": "",
            }

        autogrid_detection = ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="ok",
            version="4.2.6",
            path=str(autogrid),
            message="ok",
            source="configured",
        )
        with patch(
            "dockstart_core.autogrid.autogrid_adapter.detect",
            return_value=autogrid_detection,
        ):
            generated = generate_maps(str(project), runner=fake_autogrid)
        self.assertTrue(generated["ok"], generated)

        project_payload = json.loads(
            (project / "project.json").read_text(encoding="utf-8")
        )
        vina = parent / "vina.exe"
        vina.write_bytes(b"mock vina")
        vina_detection = SimpleNamespace(
            status="ok",
            path=str(vina),
            version="1.2.7",
            source="configured",
            message="ok",
            raw_error="",
            capabilities={},
        )
        with patch(
            "dockstart_core.screening.vina_adapter.detect",
            return_value=vina_detection,
        ):
            created = create_screening(
                str(project),
                "prepared/receptor.pdbqt",
                ["prepared/ligand.pdbqt", "prepared/ligand_two.pdbqt"],
                vina_path=str(vina),
                box=project_payload["box"],
                vina={**VINA, "scoring": "ad4"},
                max_retries=0,
                top_n=2,
            )
        self.assertTrue(created["ok"], created)
        state = created["screening"]
        self.assertEqual(state["scoring_protocol"], "ad4_maps")
        self.assertTrue(state["inputs"]["ad4_maps"]["files"])
        config = screening._config_text(state)
        self.assertIn("scoring = ad4", config)
        self.assertNotIn("receptor =", config)
        self.assertNotIn("center_x =", config)

        canceled = request_screening_cancel(str(project))
        self.assertTrue(canceled["ok"], canceled)
        map_record = state["inputs"]["ad4_maps"]["files"][0]
        map_path = project / map_record["relative_path"]
        original_map = map_path.read_bytes()
        try:
            map_path.write_bytes(original_map + b"\nmap tamper\n")
            blocked = resume_screening(str(project))
        finally:
            map_path.write_bytes(original_map)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "SCREENING_RESUME_ERROR")
        resumed = resume_screening(str(project))
        self.assertTrue(resumed["ok"], resumed)

        finished = run_screening(
            str(project),
            runner=_successful_runner([]),
        )
        self.assertTrue(finished["ok"], finished)
        first_attempt = finished["screening"]["items"][0]["attempts"][0]
        self.assertEqual(
            first_attempt["command"][
                first_attempt["command"].index("--scoring") + 1
            ],
            "ad4",
        )
        self.assertIn("--maps", first_attempt["command"])

        archived = archive_screening(str(project))
        self.assertTrue(archived["ok"], archived)
        archive_root = project / archived["archive"]
        archived_state = json.loads(
            (archive_root / "screening.json").read_text(encoding="utf-8")
        )
        self.assertEqual(archived_state["scoring_protocol"], "ad4_maps")
        self.assertTrue((archive_root / "inputs" / "ad4_maps").is_dir())

    def _assert_mid_queue_mutation_blocked(
        self,
        mutate,
        expected_code: str,
    ) -> None:
        created = self.create()
        self.assertTrue(created["ok"], created)
        calls: list[tuple[str, int]] = []
        successful = _successful_runner(calls)

        def runner(**kwargs):
            result = successful(**kwargs)
            if kwargs["item"]["item_id"] == "ligand_0001":
                mutate(created["screening"])
            return result

        response = run_screening(str(self.root), runner=runner)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], expected_code)
        self.assertEqual(calls, [("ligand_0001", 1)])
        persisted = json.loads(
            (self.root / "screening" / "screening.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(persisted["status"], "interrupted")
        self.assertEqual(persisted["queue"], ["ligand_0002"])
        self.assertEqual(persisted["items"][1]["attempt_count"], 0)
        self.assertEqual(
            persisted["last_integrity_error"]["code"],
            expected_code,
        )

    def _assert_attempt_runtime_mutation_blocked(
        self,
        mutate,
        expected_code: str,
        *,
        restore=None,
    ) -> None:
        created = self.create(ligand_files=["prepared/alpha.pdbqt"])
        self.assertTrue(created["ok"], created)
        successful = _successful_runner([])

        def runner(**kwargs):
            result = successful(**kwargs)
            mutate(kwargs)
            return result

        response = run_screening(str(self.root), runner=runner)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], expected_code)
        persisted = json.loads(
            (self.root / "screening" / "screening.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(persisted["status"], "interrupted")
        self.assertEqual(persisted["items"][0]["status"], "interrupted")
        attempt = persisted["items"][0]["attempts"][0]
        self.assertEqual(attempt["status"], "interrupted")
        self.assertEqual(attempt["integrity"]["status"], "failed")
        self.assertEqual(attempt["integrity"]["code"], expected_code)
        self.assertEqual(
            persisted["last_integrity_error"]["code"],
            expected_code,
        )
        if restore is not None:
            restore()
        resumed = resume_screening(str(self.root))
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["screening"]["status"], "ready")
        self.assertEqual(resumed["screening"]["queue"], ["ligand_0001"])
        self.assertEqual(resumed["screening"]["items"][0]["status"], "pending")

    def rewrite_archived_state(self, archive_dir: Path, update) -> dict:
        state_path = archive_dir / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        update(state)
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        manifest_path = archive_dir / "archive_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["state_sha256"] = hashlib.sha256(state_path.read_bytes()).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        return state

    def rewrite_active_state(self, update) -> dict:
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        update(state)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
        return state

    def test_create_is_deterministic_atomic_and_does_not_change_project_json(self) -> None:
        response = self.create()
        self.assertTrue(response["ok"], response)
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["source_file"] for item in state["items"]],
            ["prepared/alpha.pdbqt", "prepared/zeta.pdbqt"],
        )
        self.assertEqual(state["queue"], ["ligand_0001", "ligand_0002"])
        self.assertEqual(self.project_json.read_bytes(), self.project_before)
        self.assertFalse(any(state_path.parent.glob("*.tmp")))
        self.assertFalse(state["outputs"]["sdf"]["generated"])
        self.assertIn("原始配体拓扑", state["outputs"]["sdf"]["reason"])
        self.assertEqual(state["tools"]["vina"]["source"], "explicit")
        self.assertEqual(
            state["tools"]["vina"]["sha256"],
            hashlib.sha256(self.vina.read_bytes()).hexdigest(),
        )

    def test_stage_external_pdbqt_is_content_addressed_atomic_and_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as external_dir:
            source = Path(external_dir) / "candidate.pdbqt"
            source.write_text(_pdbqt("S"), encoding="utf-8")
            first = stage_screening_inputs(str(self.root), [str(source)])
            self.assertTrue(first["ok"], first)
            relative = first["staged"][0]["file"]
            self.assertTrue(relative.startswith("screening/staging/"))
            self.assertFalse(Path(relative).is_absolute())
            staged_path = self.root / relative
            self.assertEqual(staged_path.read_bytes(), source.read_bytes())
            self.assertFalse(any(staged_path.parent.glob("*.tmp")))

            second = stage_screening_inputs(str(self.root), [str(source)])
            self.assertTrue(second["ok"], second)
            self.assertEqual(second["staged"][0]["file"], relative)
            staged_status = get_screening_status(str(self.root))
            self.assertTrue(staged_status["ok"], staged_status)
            self.assertEqual(staged_status["mode"], "single")
            self.assertEqual(staged_status["staged"][0]["file"], relative)
            created = self.create(ligand_files=[relative])
            self.assertTrue(created["ok"], created)
            self.assertEqual(created["screening"]["items"][0]["source_file"], relative)

    def test_concurrent_staging_mutations_are_serialized(self) -> None:
        first_source = self.root / "first-external.pdbqt"
        second_source = self.root / "second-external.pdbqt"
        first_source.write_text(_pdbqt("C"), encoding="utf-8")
        second_source.write_text(_pdbqt("O"), encoding="utf-8")

        import dockstart_core.screening as screening_module

        original_expand = screening_module._expand_screening_input_files
        first_entered = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        counter_lock = threading.Lock()
        calls = 0

        def controlled_expand(files, limits):
            nonlocal calls
            with counter_lock:
                calls += 1
                call_number = calls
            if call_number == 1:
                first_entered.set()
                self.assertTrue(release_first.wait(5))
            elif call_number == 2:
                second_entered.set()
            return original_expand(files, limits)

        with (
            patch(
                "dockstart_core.screening._expand_screening_input_files",
                side_effect=controlled_expand,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first_future = pool.submit(
                stage_screening_inputs,
                str(self.root),
                [str(first_source)],
            )
            self.assertTrue(first_entered.wait(2))
            second_future = pool.submit(
                stage_screening_inputs,
                str(self.root),
                [str(second_source)],
            )
            time.sleep(0.1)
            self.assertFalse(second_entered.is_set())
            release_first.set()
            first = first_future.result(timeout=5)
            second = second_future.result(timeout=5)

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertTrue(second_entered.is_set())
        self.assertNotEqual(
            first["import_preview"]["import_id"],
            second["import_preview"]["import_id"],
        )
        index = json.loads(
            (
                self.root
                / "screening"
                / "staging"
                / "index.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(index["imports"]), 2)

    def test_stage_multirecord_sdf_isolates_invalid_record_and_preserves_order(self) -> None:
        source = self.root / "library.sdf"
        source.write_bytes(
            b"first\nmock\n$$$$\n"
            b"broken\nmock\n$$$$\n"
            b"third\nmock\n$$$$\n"
        )

        def fake_worker(
            root,
            original,
            python_path,
            *,
            max_records,
            expected_sha256,
            expected_size_bytes,
            record_dir=None,
        ):
            staging_root = root / "screening" / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            temporary = tempfile.TemporaryDirectory(
                prefix=".test-library-",
                dir=staging_root,
            )
            temporary_root = Path(temporary.name)
            first = temporary_root / "record_000001.pdbqt"
            third = temporary_root / "record_000003.pdbqt"
            first.write_text(_pdbqt("C"), encoding="utf-8")
            third.write_text(_pdbqt("N"), encoding="utf-8")
            return temporary, [
                {
                    "status": "ready",
                    "source_record_index": 1,
                    "source_record_name": "First molecule",
                    "_pdbqt_path": first,
                    **_fake_worker_identity(original, 1),
                },
                {
                    "status": "invalid",
                    "source_record_index": 2,
                    "source_record_name": "Broken molecule",
                    **_fake_worker_identity(
                        original,
                        2,
                        include_facts=False,
                    ),
                    "error": {
                        "code": "RDKIT_RECORD_INVALID",
                        "message": "RDKit 未能读取该分子记录。",
                    },
                },
                {
                    "status": "ready",
                    "source_record_index": 3,
                    "source_record_name": "Third molecule",
                    "_pdbqt_path": third,
                    **_fake_worker_identity(original, 3),
                },
            ]

        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=fake_worker,
            ),
        ):
            response = stage_screening_inputs(str(self.root), [str(source)])

        self.assertTrue(response["ok"], response)
        self.assertEqual(
            response["import_preview"]["summary"],
            {
                "total": 3,
                "ready": 2,
                "duplicate": 0,
                "review_required": 0,
                "invalid": 1,
                "source_files": 1,
            },
        )
        self.assertEqual(
            [
                (item["source_record_index"], item["status"])
                for item in response["import_preview"]["candidates"]
            ],
            [(1, "ready"), (2, "invalid"), (3, "ready")],
        )
        self.assertEqual(len(response["staged"]), 2)
        self.assertEqual(
            response["import_preview"]["candidates"][1]["error"]["code"],
            "RDKIT_RECORD_INVALID",
        )

    def test_stage_multirecord_sdf_deduplicates_output_and_freezes_sources(self) -> None:
        source = self.root / "duplicates.sdf"
        source.write_bytes(
            b"first\nmock\n$$$$\n"
            b"same chemistry\nmock\n$$$$\n"
        )

        def fake_worker(
            root,
            original,
            python_path,
            *,
            max_records,
            expected_sha256,
            expected_size_bytes,
            record_dir=None,
        ):
            staging_root = root / "screening" / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            temporary = tempfile.TemporaryDirectory(
                prefix=".test-library-",
                dir=staging_root,
            )
            temporary_root = Path(temporary.name)
            first = temporary_root / "record_000001.pdbqt"
            second = temporary_root / "record_000002.pdbqt"
            first.write_text(_pdbqt("C"), encoding="utf-8")
            second.write_text(_pdbqt("C"), encoding="utf-8")
            return temporary, [
                {
                    "status": "ready",
                    "source_record_index": 1,
                    "source_record_name": "First",
                    "_pdbqt_path": first,
                    **_fake_worker_identity(
                        original,
                        1,
                        canonical_sha256="c" * 64,
                    ),
                },
                {
                    "status": "ready",
                    "source_record_index": 2,
                    "source_record_name": "Duplicate",
                    "_pdbqt_path": second,
                    **_fake_worker_identity(
                        original,
                        2,
                        canonical_sha256="c" * 64,
                    ),
                },
            ]

        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=fake_worker,
            ),
        ):
            staged = stage_screening_inputs(str(self.root), [str(source)])

        self.assertTrue(staged["ok"], staged)
        self.assertEqual(len(staged["staged"]), 1)
        candidates = staged["import_preview"]["candidates"]
        self.assertEqual([item["status"] for item in candidates], ["ready", "duplicate"])
        self.assertEqual(candidates[1]["duplicate_of"], candidates[0]["candidate_id"])
        index = json.loads(
            (self.root / "screening" / "staging" / "index.json").read_text(
                encoding="utf-8"
            )
        )
        canonical = next(iter(index["files"].values()))
        self.assertEqual(len(canonical["source_records"]), 2)

        created = self.create(ligand_files=[staged["staged"][0]["file"]])
        self.assertTrue(created["ok"], created)
        item = created["screening"]["items"][0]
        self.assertEqual(item["source_record_name"], "First")
        self.assertEqual(item["display_label"], "First")
        self.assertEqual(len(item["source_records"]), 2)
        finished = run_screening(
            str(self.root),
            runner=_successful_runner([]),
        )
        self.assertTrue(finished["ok"], finished)
        attempt = finished["screening"]["items"][0]["attempts"][0]
        ligand_snapshot = attempt["input_snapshots"]["ligand"]
        self.assertEqual(ligand_snapshot["source_record_name"], "First")
        self.assertEqual(len(ligand_snapshot["source_records"]), 2)

    def test_stage_all_invalid_records_returns_preview_without_queue_inputs(self) -> None:
        source = self.root / "invalid.sdf"
        source.write_bytes(b"bad one\nmock\n$$$$\nbad two\nmock\n$$$$\n")

        def fake_worker(
            root,
            original,
            python_path,
            *,
            max_records,
            expected_sha256,
            expected_size_bytes,
            record_dir=None,
        ):
            staging_root = root / "screening" / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            temporary = tempfile.TemporaryDirectory(
                prefix=".test-library-",
                dir=staging_root,
            )
            return temporary, [
                {
                    "status": "invalid",
                    "source_record_index": index,
                    "source_record_name": f"Bad {index}",
                    **_fake_worker_identity(
                        original,
                        index,
                        include_facts=False,
                    ),
                    "error": {
                        "code": "RDKIT_RECORD_INVALID",
                        "message": "RDKit 未能读取该分子记录。",
                    },
                }
                for index in (1, 2)
            ]

        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=fake_worker,
            ),
        ):
            response = stage_screening_inputs(str(self.root), [str(source)])

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["staged"], [])
        self.assertEqual(response["import_preview"]["summary"]["invalid"], 2)
        status = get_screening_status(str(self.root))
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["staged"], [])
        self.assertEqual(
            status["import_preview"]["summary"]["invalid"],
            2,
        )

    def test_stage_multirecord_limit_blocks_preparation_worker(self) -> None:
        source = self.root / "too-many.sdf"
        source.write_bytes(b"one\nmock\n$$$$\ntwo\nmock\n$$$$\n")
        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library"
            ) as worker,
        ):
            response = stage_screening_inputs(
                str(self.root),
                [str(source)],
                resource_limits={"max_ligands": 1},
            )
        self.assertFalse(response["ok"])
        self.assertIn("配体记录数量超过资源上限", response["error"]["raw_error"])
        worker.assert_not_called()

    def test_stage_directory_import_uses_supported_files_in_stable_order(self) -> None:
        directory = self.root / "library"
        (directory / "nested").mkdir(parents=True)
        (directory / "zeta.pdbqt").write_text(_pdbqt("Z"), encoding="utf-8")
        (directory / "alpha.pdbqt").write_text(_pdbqt("A"), encoding="utf-8")
        (directory / "nested" / "beta.pdbqt").write_text(
            _pdbqt("B"),
            encoding="utf-8",
        )
        (directory / "notes.txt").write_text("ignored", encoding="utf-8")

        response = stage_screening_inputs(str(self.root), [str(directory)])

        self.assertTrue(response["ok"], response)
        self.assertEqual(
            [
                item["original_name"]
                for item in response["import_preview"]["candidates"]
            ],
            ["alpha.pdbqt", "zeta.pdbqt", "beta.pdbqt"],
        )
        self.assertEqual(len(response["staged"]), 3)

    def test_stage_v2_freezes_every_raw_record_and_preserves_failure_states(self) -> None:
        source = self.root / "audited-library.sdf"
        records = [
            b"ready\nmock\n$$$$\n",
            b"broken\nmock\n$$$$\n",
            b"macrocycle\nmock\n$$$$\n",
        ]
        source.write_bytes(b"".join(records))
        worker = _fake_library_worker(
            [
                {"index": 1, "status": "ready", "atom": "C"},
                {
                    "index": 2,
                    "status": "invalid",
                    "include_facts": False,
                    "error": {
                        "code": "RDKIT_RECORD_INVALID",
                        "message": "无法解析。",
                    },
                },
                {
                    "index": 3,
                    "status": "invalid",
                    "has_macrocycle": True,
                    "error": {
                        "code": "LIGAND_PREPARATION_FAILED",
                        "message": "Meeko 在大环上失败。",
                    },
                },
            ]
        )
        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=worker,
            ),
        ):
            response = stage_screening_inputs(str(self.root), [str(source)])

        self.assertTrue(response["ok"], response)
        preview = response["import_preview"]
        self.assertEqual(preview["schema_version"], 2)
        self.assertEqual(
            preview["summary"],
            {
                "total": 3,
                "ready": 1,
                "duplicate": 0,
                "review_required": 1,
                "invalid": 1,
                "source_files": 1,
            },
        )
        self.assertEqual(
            [candidate["status"] for candidate in preview["candidates"]],
            ["ready", "invalid", "review_required"],
        )
        self.assertFalse(preview["candidates"][2]["retryable"])
        self.assertEqual(
            preview["candidates"][2]["error"]["code"],
            "MACROCYCLE_REVIEW_REQUIRED",
        )
        for raw, candidate in zip(records, preview["candidates"], strict=True):
            topology = self.root / candidate["source_topology_file"]
            self.assertEqual(topology.read_bytes(), raw)
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(),
                candidate["source_topology_sha256"],
            )
            self.assertEqual(candidate["topology_integrity"], "verified")
        failure_manifest = preview["failure_manifest"]
        failure_json = self.root / failure_manifest["json_file"]
        self.assertEqual(
            hashlib.sha256(failure_json.read_bytes()).hexdigest(),
            failure_manifest["json_sha256"],
        )
        failures = json.loads(failure_json.read_text(encoding="utf-8"))
        self.assertEqual(failures["failure_count"], 2)

    def test_stage_rejects_worker_record_identity_mismatch(self) -> None:
        source = self.root / "identity-mismatch.sdf"
        source.write_bytes(b"molecule\nmock\n$$$$\n")
        worker = _fake_library_worker([{"index": 1, "status": "ready"}])

        def mismatched_worker(*args, **kwargs):
            temporary, records = worker(*args, **kwargs)
            records[0]["source_record_sha256"] = "f" * 64
            return temporary, records

        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=mismatched_worker,
            ),
        ):
            response = stage_screening_inputs(str(self.root), [str(source)])

        self.assertFalse(response["ok"])
        self.assertIn("冻结原始记录身份不一致", response["error"]["raw_error"])
        import_manifests = list(
            (self.root / "screening" / "staging" / "imports").glob(
                "import_*/import_manifest.json"
            )
        )
        self.assertEqual(len(import_manifests), 1)
        self.assertEqual(
            json.loads(import_manifests[0].read_text(encoding="utf-8"))[
                "status"
            ],
            "failed",
        )

    def test_stage_rejects_unknown_worker_preparation_profile(self) -> None:
        source = self.root / "profile-mismatch.sdf"
        source.write_bytes(b"molecule\nmock\n$$$$\n")
        worker = _fake_library_worker([{"index": 1, "status": "ready"}])

        def mismatched_worker(*args, **kwargs):
            temporary, records = worker(*args, **kwargs)
            records[0]["_worker_evidence"]["preparation_profile"] = (
                "untrusted-preparation-profile"
            )
            return temporary, records

        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=mismatched_worker,
            ),
        ):
            response = stage_screening_inputs(str(self.root), [str(source)])

        self.assertFalse(response["ok"])
        self.assertIn("准备规范不受支持", response["error"]["raw_error"])

    def test_stage_rejects_rdkit_version_mismatch_between_facts_and_evidence(
        self,
    ) -> None:
        source = self.root / "rdkit-version-mismatch.sdf"
        source.write_bytes(b"molecule\nmock\n$$$$\n")
        worker = _fake_library_worker([{"index": 1, "status": "ready"}])

        def mismatched_worker(*args, **kwargs):
            temporary, records = worker(*args, **kwargs)
            records[0]["_worker_evidence"]["toolchain"]["rdkit_version"] = (
                "different-rdkit"
            )
            return temporary, records

        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=mismatched_worker,
            ),
        ):
            response = stage_screening_inputs(str(self.root), [str(source)])

        self.assertFalse(response["ok"])
        self.assertIn("RDKit 版本不一致", response["error"]["raw_error"])

    def test_candidate_creation_binds_distinct_topologies_with_same_pdbqt(self) -> None:
        source = self.root / "same-pdbqt-different-topology.sdf"
        raw_records = [
            b"topology one\nmock\n$$$$\n",
            b"topology two\nmock\n$$$$\n",
        ]
        source.write_bytes(b"".join(raw_records))
        worker = _fake_library_worker(
            [
                {
                    "index": 1,
                    "status": "ready",
                    "atom": "C",
                    "canonical_sha256": "1" * 64,
                },
                {
                    "index": 2,
                    "status": "ready",
                    "atom": "C",
                    "canonical_sha256": "2" * 64,
                },
            ]
        )
        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=worker,
            ),
        ):
            staged = stage_screening_inputs(str(self.root), [str(source)])
        self.assertTrue(staged["ok"], staged)
        candidates = staged["import_preview"]["candidates"]
        self.assertEqual([item["status"] for item in candidates], ["ready", "ready"])
        self.assertEqual(candidates[0]["sha256"], candidates[1]["sha256"])

        created = self.create(
            ligand_files=[],
            ligand_candidate_ids=[
                candidate["candidate_id"] for candidate in candidates
            ],
            expected_staging_revision_sha256=staged["import_preview"][
                "revision_sha256"
            ],
        )
        self.assertTrue(created["ok"], created)
        items = created["screening"]["items"]
        self.assertEqual(len(items), 2)
        self.assertNotEqual(
            items[0]["source_topology_sha256"],
            items[1]["source_topology_sha256"],
        )
        for raw, item in zip(raw_records, items, strict=True):
            self.assertEqual(
                (self.root / item["source_topology_file"]).read_bytes(),
                raw,
            )
            self.assertTrue(item["source_candidate_id"])
            self.assertTrue(item["source_record_id"])
        self.assertEqual(
            created["screening"]["inputs"]["topology"]["status"],
            "complete",
        )

        finished = run_screening(
            str(self.root),
            runner=_successful_runner([]),
        )
        self.assertTrue(finished["ok"], finished)
        for item in finished["screening"]["items"]:
            topology_snapshot = item["attempts"][0]["input_snapshots"][
                "source_topology"
            ]
            self.assertEqual(
                topology_snapshot["sha256"],
                item["source_topology_sha256"],
            )

    def test_batch_result_sdf_is_topology_validated_annotated_and_archivable(self) -> None:
        source = self.root / "result-export-source.sdf"
        source.write_bytes(b"source topology\nmock\n$$$$\n")
        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=_fake_library_worker(
                    [{"index": 1, "status": "ready", "atom": "C"}]
                ),
            ),
        ):
            staged = stage_screening_inputs(str(self.root), [str(source)])
        candidate = staged["import_preview"]["candidates"][0]
        created = self.create(
            ligand_files=[],
            ligand_candidate_ids=[candidate["candidate_id"]],
            expected_staging_revision_sha256=staged["import_preview"][
                "revision_sha256"
            ],
        )
        self.assertTrue(created["ok"], created)
        finished = run_screening(
            str(self.root),
            runner=_successful_runner([]),
        )
        self.assertTrue(finished["ok"], finished)

        def fake_mk_export(
            python_path,
            output_pdbqt,
            output_sdf,
            *,
            record_dir,
            cwd,
        ):
            output_sdf.parent.mkdir(parents=True, exist_ok=True)
            output_sdf.write_bytes(
                b"pose one\nmock\n$$$$\npose two\nmock\n$$$$\n"
            )
            record_dir.mkdir(parents=True)
            (record_dir / "stdout.txt").write_text("", encoding="utf-8")
            (record_dir / "stderr.txt").write_text("", encoding="utf-8")
            (record_dir / "command_result.json").write_text(
                '{"status":"success"}',
                encoding="utf-8",
            )
            return {"status": "success"}

        def fake_validator(
            root,
            python_path,
            topology_path,
            exported_sdf,
            expected_graph_sha256,
            *,
            record_dir,
        ):
            record_dir.mkdir(parents=True)
            manifest = {
                "schema_version": 1,
                "status": "verified",
                "rdkit_version": "test-rdkit",
                "pose_count": 2,
                "all_poses_match": True,
            }
            (record_dir / "validation_manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            return manifest

        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening.inspect_meeko_ligand_pdbqt",
                return_value={"embedded_topology": True},
            ),
        ):
            exported = generate_screening_result_sdf(
                str(self.root),
                mk_export_executor=fake_mk_export,
                topology_validator=fake_validator,
            )
        self.assertTrue(exported["ok"], exported)
        sdf = exported["sdf"]
        self.assertTrue(sdf["generated"])
        self.assertEqual(sdf["status"], "complete")
        self.assertEqual(sdf["topology_coverage"]["pose_count"], 2)
        self.assertEqual(
            sdf["toolchain"]["profile"],
            "meeko_export_rdkit_heavy_graph_validation_v1",
        )
        self.assertEqual(sdf["toolchain"]["rdkit"]["version"], "test-rdkit")
        self.assertRegex(
            sdf["toolchain"]["snapshot_sha256"],
            r"^[0-9a-f]{64}$",
        )
        aggregate = (self.root / sdf["file"]).read_text(encoding="utf-8")
        self.assertEqual(aggregate.count("$$$$"), 2)
        self.assertIn("<DOCKSTART_ITEM_ID>", aggregate)
        self.assertIn("<DOCKSTART_SOURCE_TOPOLOGY_SHA256>", aggregate)
        self.assertEqual(
            hashlib.sha256((self.root / sdf["file"]).read_bytes()).hexdigest(),
            sdf["sha256"],
        )
        manifest = json.loads(
            (self.root / sdf["manifest_file"]).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["toolchain"], sdf["toolchain"])
        reused = generate_screening_result_sdf(str(self.root))
        self.assertTrue(reused["ok"], reused)
        manifest_path = self.root / sdf["manifest_file"]
        manifest_bytes = manifest_path.read_bytes()
        manifest_path.write_text("{}\n", encoding="utf-8")
        tampered = generate_screening_result_sdf(str(self.root))
        self.assertFalse(tampered["ok"])
        self.assertEqual(
            tampered["error"]["code"],
            "SCREENING_RESULT_SDF_ERROR",
        )
        self.assertIn("完整性校验失败", tampered["error"]["raw_error"])
        manifest_path.write_bytes(manifest_bytes)

        archived = archive_screening(str(self.root))
        self.assertTrue(archived["ok"], archived)
        with tempfile.TemporaryDirectory() as destination_dir:
            destination = Path(destination_dir) / "audited-screening.zip"
            zipped = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )
        self.assertTrue(zipped["ok"], zipped)

    def test_result_sdf_annotations_are_separate_properties_and_preserve_zero(
        self,
    ) -> None:
        from dockstart_core.screening import _annotated_screening_sdf_records

        payload = (
            b"pose\n"
            b"mock\n"
            b">  <meeko>\n"
            b"{\"is_sidechain\": [false]}\n\n"
            b"$$$$\n"
        )
        annotated, count = _annotated_screening_sdf_records(
            payload,
            {
                "DOCKSTART_ITEM_ID": "ligand_0001",
                "DOCKSTART_BEST_AFFINITY_KCAL_MOL": 0.0,
            },
        )

        self.assertEqual(count, 1)
        self.assertIn(
            b"{\"is_sidechain\": [false]}\n\n"
            b">  <DOCKSTART_ITEM_ID>\nligand_0001\n\n",
            annotated,
        )
        self.assertIn(
            b">  <DOCKSTART_BEST_AFFINITY_KCAL_MOL>\n0.0\n\n",
            annotated,
        )

    def test_batch_result_sdf_excludes_topology_mismatch(self) -> None:
        source = self.root / "result-mismatch-source.sdf"
        source.write_bytes(b"source topology\nmock\n$$$$\n")
        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=_fake_library_worker(
                    [{"index": 1, "status": "ready", "atom": "C"}]
                ),
            ),
        ):
            staged = stage_screening_inputs(str(self.root), [str(source)])
        candidate = staged["import_preview"]["candidates"][0]
        created = self.create(
            ligand_files=[],
            ligand_candidate_ids=[candidate["candidate_id"]],
            expected_staging_revision_sha256=staged["import_preview"][
                "revision_sha256"
            ],
        )
        self.assertTrue(created["ok"], created)
        self.assertTrue(
            run_screening(
                str(self.root),
                runner=_successful_runner([]),
            )["ok"]
        )

        def fake_mk_export(
            python_path,
            output_pdbqt,
            output_sdf,
            *,
            record_dir,
            cwd,
        ):
            output_sdf.parent.mkdir(parents=True, exist_ok=True)
            output_sdf.write_bytes(b"wrong topology\nmock\n$$$$\n")
            record_dir.mkdir(parents=True)
            return {"status": "success"}

        def rejecting_validator(*args, **kwargs):
            record_dir = kwargs["record_dir"]
            record_dir.mkdir(parents=True)
            raise ValueError("导出构象的重原子图与冻结拓扑不一致")

        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening.inspect_meeko_ligand_pdbqt",
                return_value={"embedded_topology": True},
            ),
        ):
            response = generate_screening_result_sdf(
                str(self.root),
                mk_export_executor=fake_mk_export,
                topology_validator=rejecting_validator,
            )
        self.assertFalse(response["ok"])
        self.assertFalse(response["sdf"]["generated"])
        self.assertEqual(response["sdf"]["status"], "failed")
        self.assertIn(
            "冻结拓扑不一致",
            response["sdf"]["failures"][0]["message"],
        )
        self.assertFalse(
            (self.root / "screening" / "results" / "screening_poses.sdf").exists()
        )

    def test_candidate_creation_rejects_stale_revision_without_orphans(self) -> None:
        first_source = self.root / "first-import.pdbqt"
        second_source = self.root / "second-import.pdbqt"
        first_source.write_text(_pdbqt("C"), encoding="utf-8")
        second_source.write_text(_pdbqt("N"), encoding="utf-8")
        first = stage_screening_inputs(str(self.root), [str(first_source)])
        second = stage_screening_inputs(str(self.root), [str(second_source)])
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)

        response = self.create(
            ligand_files=[],
            ligand_candidate_ids=[
                first["import_preview"]["candidates"][0]["candidate_id"]
            ],
            expected_staging_revision_sha256=first["import_preview"][
                "revision_sha256"
            ],
        )
        self.assertFalse(response["ok"])
        self.assertIn("已经变化", response["error"]["raw_error"])
        self.assertFalse((self.root / "screening" / "inputs").exists())

    def test_candidate_creation_rejects_topology_tamper_without_orphans(self) -> None:
        source = self.root / "tamper-topology.sdf"
        source.write_bytes(b"molecule\nmock\n$$$$\n")
        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=_fake_library_worker(
                    [{"index": 1, "status": "ready"}]
                ),
            ),
        ):
            staged = stage_screening_inputs(str(self.root), [str(source)])
        self.assertTrue(staged["ok"], staged)
        candidate = staged["import_preview"]["candidates"][0]
        (self.root / candidate["source_topology_file"]).write_bytes(b"tampered")

        response = self.create(
            ligand_files=[],
            ligand_candidate_ids=[candidate["candidate_id"]],
            expected_staging_revision_sha256=staged["import_preview"][
                "revision_sha256"
            ],
        )
        self.assertFalse(response["ok"])
        self.assertIn("完整性校验失败", response["error"]["raw_error"])
        self.assertFalse((self.root / "screening" / "inputs").exists())

    def test_stage_deduplicates_same_resolved_source_path(self) -> None:
        directory = self.root / "deduplicate-source"
        directory.mkdir()
        source = directory / "same.pdbqt"
        source.write_text(_pdbqt("C"), encoding="utf-8")
        response = stage_screening_inputs(
            str(self.root),
            [str(source), str(directory)],
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["import_preview"]["summary"]["total"], 1)
        self.assertEqual(len(response["staged"]), 1)

    def test_retry_preparation_uses_frozen_record_and_appends_attempt(self) -> None:
        source = self.root / "retry-source.sdf"
        raw = b"retry molecule\nmock\n$$$$\n"
        source.write_bytes(raw)
        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=_fake_library_worker(
                    [
                        {
                            "index": 1,
                            "status": "invalid",
                            "error": {
                                "code": "LIGAND_PREPARATION_FAILED",
                                "message": "首次准备失败。",
                            },
                        }
                    ]
                ),
            ),
        ):
            staged = stage_screening_inputs(str(self.root), [str(source)])
        self.assertTrue(staged["ok"], staged)
        candidate = staged["import_preview"]["candidates"][0]
        self.assertTrue(candidate["retryable"])
        self.assertEqual(len(candidate["preparation_attempts"]), 1)
        old_revision = staged["import_preview"]["revision_sha256"]

        source.unlink()
        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=_fake_library_worker(
                    [{"index": 1, "status": "ready", "atom": "N"}]
                ),
            ),
        ):
            retried = retry_screening_preparation(
                str(self.root),
                [candidate["candidate_id"]],
                expected_staging_revision_sha256=old_revision,
            )
        self.assertTrue(retried["ok"], retried)
        updated = retried["import_preview"]["candidates"][0]
        self.assertEqual(updated["status"], "ready")
        self.assertFalse(updated["retryable"])
        self.assertEqual(len(updated["preparation_attempts"]), 2)
        self.assertEqual(
            [attempt["attempt"] for attempt in updated["preparation_attempts"]],
            [1, 2],
        )
        self.assertNotEqual(
            retried["import_preview"]["revision_sha256"],
            old_revision,
        )
        self.assertEqual(
            (self.root / updated["source_topology_file"]).read_bytes(),
            raw,
        )

        created = self.create(
            ligand_files=[],
            ligand_candidate_ids=[updated["candidate_id"]],
            expected_staging_revision_sha256=retried["import_preview"][
                "revision_sha256"
            ],
        )
        self.assertTrue(created["ok"], created)

    def test_retry_execution_failure_is_recorded_without_losing_raw_record(self) -> None:
        source = self.root / "retry-execution-failure.sdf"
        raw = b"retry failure\nmock\n$$$$\n"
        source.write_bytes(raw)
        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=_fake_library_worker(
                    [
                        {
                            "index": 1,
                            "status": "invalid",
                            "error": {
                                "code": "LIGAND_PREPARATION_FAILED",
                                "message": "首次准备失败。",
                            },
                        }
                    ]
                ),
            ),
        ):
            staged = stage_screening_inputs(str(self.root), [str(source)])
        candidate = staged["import_preview"]["candidates"][0]

        with (
            patch(
                "dockstart_core.screening.get_preparation_tool_status",
                return_value=_screening_preparation_tools(),
            ),
            patch(
                "dockstart_core.screening._prepare_raw_screening_library",
                side_effect=RuntimeError("worker crashed"),
            ),
        ):
            retried = retry_screening_preparation(
                str(self.root),
                [candidate["candidate_id"]],
                expected_staging_revision_sha256=staged["import_preview"][
                    "revision_sha256"
                ],
            )
        self.assertTrue(retried["ok"], retried)
        updated = retried["import_preview"]["candidates"][0]
        self.assertEqual(updated["status"], "invalid")
        self.assertTrue(updated["retryable"])
        self.assertEqual(
            updated["error"]["code"],
            "LIGAND_PREPARATION_RETRY_EXECUTION_FAILED",
        )
        self.assertEqual(len(updated["preparation_attempts"]), 2)
        self.assertEqual(
            (self.root / updated["source_topology_file"]).read_bytes(),
            raw,
        )

    def test_create_without_vina_path_uses_settings_detection_and_records_tool(self) -> None:
        detection = SimpleNamespace(
            status="ok",
            path=str(self.vina),
            version="1.2.7",
            source="configured",
            message="ok",
        )
        settings = SimpleNamespace(tool_paths=SimpleNamespace(vina=str(self.vina)))
        with (
            patch("dockstart_core.screening.load_settings", return_value=settings),
            patch("dockstart_core.screening.vina_adapter.detect", return_value=detection) as detect,
        ):
            response = self.create(vina_path=None, mock_detection=False)
        self.assertTrue(response["ok"], response)
        detect.assert_called_once_with(str(self.vina))
        tool = response["screening"]["tools"]["vina"]
        self.assertEqual(tool["version"], "1.2.7")
        self.assertEqual(tool["source"], "configured")
        self.assertEqual(tool["detection_status"], "ok")

    def test_explicit_vina_path_must_pass_adapter_detection(self) -> None:
        response = self.create(mock_detection=False)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "SCREENING_CREATE_ERROR")
        self.assertFalse((self.root / "screening" / "screening.json").exists())

    def test_create_freezes_advanced_vina_params_and_conservative_grid_estimate(self) -> None:
        multi_type = self.root / "prepared" / "multi-type.pdbqt"
        multi_type.write_text(
            _pdbqt("C")
            + _pdbqt("N").replace(" 0.000 C\n", " 0.000 N\n"),
            encoding="utf-8",
        )
        advanced_vina = {
            **VINA,
            "max_evals": 250000,
            "min_rmsd": 1.5,
            "spacing": 0.5,
            "verbosity": 2,
            "no_refine": True,
            "force_even_voxels": True,
            "unbound_energy": -4.25,
        }
        response = self.create(
            ligand_files=["prepared/alpha.pdbqt", "prepared/multi-type.pdbqt"],
            box={**BOX, "size_x": 20.25},
            vina=advanced_vina,
            detection_capabilities=SUPPORTED_ADVANCED_CAPABILITIES,
        )
        self.assertTrue(response["ok"], response)
        frozen = response["screening"]["vina"]
        self.assertEqual(frozen["max_evals"], 250000)
        self.assertEqual(frozen["min_rmsd"], 1.5)
        self.assertEqual(frozen["spacing"], 0.5)
        self.assertEqual(frozen["verbosity"], 2)
        self.assertTrue(frozen["no_refine"])
        self.assertTrue(frozen["force_even_voxels"])
        self.assertNotIn("unbound_energy", frozen)

        grid = response["screening"]["grid_resource"]
        self.assertEqual(
            grid["reference_ligand"]["source_file"],
            "prepared/multi-type.pdbqt",
        )
        self.assertEqual(grid["reference_ligand"]["atom_type_count"], 2)
        self.assertEqual(grid["estimate"]["map_count"], 2)
        self.assertEqual(grid["estimate"]["spacing_angstrom"], 0.5)
        self.assertTrue(grid["estimate"]["force_even_voxels"])
        self.assertIn("x", grid["estimate"]["adjusted_axes"])
        self.assertTrue(grid["warnings"])

    def test_invalid_advanced_vina_params_are_rejected_before_state_write(self) -> None:
        invalid_values = (
            ("max_evals", -1),
            ("max_evals", 1.5),
            ("max_evals", float("inf")),
            ("min_rmsd", -0.1),
            ("min_rmsd", float("nan")),
            ("spacing", 0.05),
            ("spacing", float("inf")),
            ("verbosity", 0),
            ("no_refine", "true"),
            ("force_even_voxels", 1),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value):
                response = self.create(vina={**VINA, field: value})
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "SCREENING_CREATE_ERROR")
                self.assertFalse(
                    (self.root / "screening" / "screening.json").exists()
                )

    def test_base_numeric_params_reject_bool_and_fractional_integers(self) -> None:
        cases = (
            ("center_bool", {**BOX, "center_x": True}, VINA),
            ("size_bool", {**BOX, "size_x": False}, VINA),
            ("energy_bool", BOX, {**VINA, "energy_range": True}),
            ("exhaustiveness_fraction", BOX, {**VINA, "exhaustiveness": 8.5}),
            ("num_modes_fraction", BOX, {**VINA, "num_modes": 9.25}),
            ("cpu_fraction", BOX, {**VINA, "cpu": 1.5}),
            ("seed_fraction", BOX, {**VINA, "seed": 12.5}),
            ("cpu_non_finite", BOX, {**VINA, "cpu": float("inf")}),
        )
        for label, box, vina in cases:
            with self.subTest(label=label):
                response = self.create(box=box, vina=vina)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "SCREENING_CREATE_ERROR")
                self.assertFalse(
                    (self.root / "screening" / "screening.json").exists()
                )

    def test_creation_rejects_invalid_or_relaxed_resource_limit_policy(self) -> None:
        cases = (
            {"max_ligands": True},
            {"max_retries": 1.0},
            {"max_cpu": "64"},
            {"max_exhaustiveness": 129},
            {"max_num_modes": 51},
            {"max_box_edge_angstrom": float("inf")},
            {"max_ligand_bytes": 16 * 1024 * 1024 + 1},
            {"max_staged_file_bytes": 128 * 1024 * 1024 + 1},
            {"max_total_input_bytes": 512 * 1024 * 1024 + 1},
            {"unknown_limit": 1},
        )
        for resource_limits in cases:
            with self.subTest(resource_limits=resource_limits):
                response = self.create(resource_limits=resource_limits)
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "SCREENING_CREATE_ERROR",
                )
                self.assertFalse(
                    (self.root / "screening" / "screening.json").exists()
                )
        for field, value in (("max_retries", True), ("top_n", False)):
            with self.subTest(field=field, value=value):
                response = self.create(**{field: value})
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "SCREENING_CREATE_ERROR",
                )

    def test_create_rejects_unsupported_advanced_vina_switch(self) -> None:
        unsupported = {
            "features": {
                "no_refine": {
                    "status": "unsupported",
                    "supported": False,
                    "message": "missing --no_refine",
                },
                "force_even_voxels": {
                    "status": "supported",
                    "supported": True,
                    "message": "supported",
                },
            }
        }
        response = self.create(
            vina={**VINA, "no_refine": True},
            detection_capabilities=unsupported,
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "VINA_NO_REFINE_UNSUPPORTED")
        self.assertFalse((self.root / "screening" / "screening.json").exists())

    def test_grid_resource_hard_limit_blocks_screening_creation(self) -> None:
        response = self.create(
            box={
                "center_x": 0,
                "center_y": 0,
                "center_z": 0,
                "size_x": 126,
                "size_y": 126,
                "size_z": 126,
            },
            vina={**VINA, "spacing": 0.1},
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VINA_GRID_RESOURCE_LIMIT_EXCEEDED",
        )
        self.assertFalse((self.root / "screening" / "screening.json").exists())

    def test_grid_resource_uses_four_map_fallback_for_unparsed_atom_types(self) -> None:
        unknown_types = self.root / "prepared" / "unknown-types.pdbqt"
        unknown_types.write_text(
            "REMARK nonempty legacy PDBQT without parseable atom types\n",
            encoding="utf-8",
        )
        response = self.create(
            ligand_files=[
                "prepared/alpha.pdbqt",
                "prepared/unknown-types.pdbqt",
            ],
            box={
                "center_x": 0,
                "center_y": 0,
                "center_z": 0,
                "size_x": 126,
                "size_y": 126,
                "size_z": 126,
            },
            vina={**VINA, "spacing": 0.25},
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VINA_GRID_RESOURCE_LIMIT_EXCEEDED",
        )
        self.assertFalse((self.root / "screening" / "screening.json").exists())

    def test_new_screening_rejects_energy_range_zero(self) -> None:
        response = self.create(vina={**VINA, "energy_range": 0})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "SCREENING_CREATE_ERROR")
        self.assertFalse((self.root / "screening" / "screening.json").exists())

    def test_legacy_active_energy_zero_survives_interruption_and_resume(self) -> None:
        created = self.create()
        self.assertTrue(created["ok"], created)
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        for key in (
            "max_evals",
            "min_rmsd",
            "spacing",
            "verbosity",
            "no_refine",
            "force_even_voxels",
        ):
            state["vina"].pop(key, None)
        state["vina"]["energy_range"] = 0
        state.pop("grid_resource", None)
        state.pop("parameter_warnings", None)
        state.pop("compatibility", None)
        state["tools"]["vina"].pop("capabilities", None)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )

        calls: list[tuple[str, int]] = []
        interrupted = run_screening(
            str(self.root),
            runner=_successful_runner(calls),
            max_items=1,
        )
        self.assertTrue(interrupted["ok"], interrupted)
        self.assertEqual(interrupted["screening"]["status"], "interrupted")
        marker = interrupted["screening"]["compatibility"][
            "legacy_energy_range_zero"
        ]
        self.assertTrue(marker["applied"])
        self.assertEqual(interrupted["screening"]["vina"]["energy_range"], 0.0)
        self.assertEqual(interrupted["screening"]["vina"]["max_evals"], 0)

        resumed = resume_screening(str(self.root))
        self.assertTrue(resumed["ok"], resumed)
        self.assertTrue(
            resumed["screening"]["compatibility"]["legacy_energy_range_zero"][
                "applied"
            ]
        )
        finished = run_screening(str(self.root), runner=_successful_runner(calls))
        self.assertTrue(finished["ok"], finished)
        self.assertEqual(finished["screening"]["status"], "completed")
        self.assertEqual(
            calls,
            [("ligand_0001", 1), ("ligand_0002", 1)],
        )
        for item_id in ("ligand_0001", "ligand_0002"):
            config = (
                self.root
                / "screening"
                / "attempts"
                / item_id
                / "attempt_001"
                / "config.txt"
            ).read_text(encoding="utf-8")
            self.assertIn("energy_range = 0", config)
            self.assertIn("max_evals = 0", config)

        report = export_screening_markdown_report(str(self.root))
        self.assertTrue(report["ok"], report)
        report_text = (
            self.root / "screening" / "results" / "screening_report.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Vina 自动决定", report_text)
        self.assertIn("历史 schema v1", report_text)

    def test_run_writes_advanced_params_excludes_unbound_energy_and_warns(self) -> None:
        advanced_vina = {
            **VINA,
            "max_evals": 345678,
            "min_rmsd": 1.25,
            "spacing": 0.5,
            "verbosity": 2,
            "no_refine": True,
            "force_even_voxels": True,
            "unbound_energy": -3.5,
        }
        created = self.create(
            vina=advanced_vina,
            detection_capabilities=SUPPORTED_ADVANCED_CAPABILITIES,
        )
        self.assertTrue(created["ok"], created)
        self.assertTrue(created["screening"]["parameter_warnings"])
        self.assertIn(
            "显式未结合态参考能量",
            created["screening"]["parameter_warnings"][0],
        )
        detection = SimpleNamespace(
            status="ok",
            path=str(self.vina),
            version="1.2.7",
            source="explicit",
            message="ok",
            raw_error="",
            capabilities=SUPPORTED_ADVANCED_CAPABILITIES,
        )
        with patch(
            "dockstart_core.screening.vina_adapter.detect",
            return_value=detection,
        ) as detect:
            finished = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertTrue(finished["ok"], finished)
        self.assertEqual(detect.call_count, 1)
        self.assertTrue(
            finished["screening"]["grid_resource"]["execution_check"][
                "matches_recorded"
            ]
        )

        config_path = (
            self.root
            / "screening"
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "config.txt"
        )
        config = config_path.read_text(encoding="utf-8")
        for expected in (
            "max_evals = 345678",
            "min_rmsd = 1.25",
            "spacing = 0.5",
            "verbosity = 2",
            "no_refine = true",
            "force_even_voxels = true",
        ):
            self.assertIn(expected, config)
        self.assertNotIn("unbound_energy", config)
        attempt = finished["screening"]["items"][0]["attempts"][0]
        self.assertEqual(
            attempt["input_snapshots"]["receptor"]["sha256"],
            created["screening"]["inputs"]["receptor"]["sha256"],
        )
        self.assertEqual(
            attempt["input_snapshots"]["ligand"]["sha256"],
            created["screening"]["items"][0]["sha256"],
        )
        self.assertEqual(
            attempt["config_snapshot"]["sha256"],
            hashlib.sha256(config_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            attempt["vina_snapshot"]["sha256"],
            hashlib.sha256(self.vina.read_bytes()).hexdigest(),
        )

        report = export_screening_markdown_report(str(self.root))
        self.assertTrue(report["ok"], report)
        report_text = (
            self.root / "screening" / "results" / "screening_report.md"
        ).read_text(encoding="utf-8")
        for expected in (
            "345678",
            "1.25 Å",
            "0.5 Å",
            "日志详细程度：2",
            "关闭显式受体原子精修：是",
            "网格体素数取偶数：是",
        ):
            self.assertIn(expected, report_text)
        self.assertNotIn("unbound_energy", report_text)
        self.assertIn("显式未结合态参考能量", report_text)

    def test_run_fails_closed_when_frozen_vina_binary_changes(self) -> None:
        created = self.create(ligand_files=["prepared/alpha.pdbqt"])
        self.assertTrue(created["ok"], created)
        self.vina.write_bytes(b"changed binary")
        response = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_VINA_BINARY_CHANGED",
        )
        state = json.loads(
            (self.root / "screening" / "screening.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["items"][0]["attempt_count"], 0)

    def test_run_preflight_rejects_tampered_frozen_ligand(self) -> None:
        created = self.create(ligand_files=["prepared/alpha.pdbqt"])
        self.assertTrue(created["ok"], created)
        frozen_ligand = (
            self.root
            / created["screening"]["items"][0]["ligand_file"]
        )
        frozen_ligand.write_text(
            frozen_ligand.read_text(encoding="utf-8") + "REMARK changed\n",
            encoding="utf-8",
        )
        response = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_EXECUTION_PREFLIGHT_ERROR",
        )
        self.assertIn("冻结配体", response["error"]["raw_error"])

    def test_run_preflight_revalidates_complete_frozen_resource_policy(self) -> None:
        created = self.create()
        self.assertTrue(created["ok"], created)
        state_path = self.root / "screening" / "screening.json"
        original = state_path.read_bytes()
        cases = (
            "limit_bool",
            "limit_relaxed_above_hard_max",
            "limit_hash_mismatch",
            "max_retries_bool",
            "max_retries_above_limit",
            "top_n_bool",
            "top_n_above_limit",
            "item_count",
            "ligand_size_record_type",
            "ligand_size_limit",
            "receptor_size_record_type",
            "total_input_limit",
        )
        for case in cases:
            with self.subTest(case=case):
                state_path.write_bytes(original)

                def mutate(state, case=case):
                    if case == "limit_bool":
                        state["resource_limits"]["max_staged_file_bytes"] = True
                    elif case == "limit_relaxed_above_hard_max":
                        state["resource_limits"]["max_ligands"] = 501
                    elif case == "limit_hash_mismatch":
                        state["resource_limits"]["max_ligands"] = 499
                    elif case == "max_retries_bool":
                        state["max_retries"] = True
                    elif case == "max_retries_above_limit":
                        state["max_retries"] = (
                            state["resource_limits"]["max_retries"] + 1
                        )
                    elif case == "top_n_bool":
                        state["top_n"] = False
                    elif case == "top_n_above_limit":
                        state["top_n"] = (
                            state["resource_limits"]["max_ligands"] + 1
                        )
                    elif case == "item_count":
                        state["resource_limits"]["max_ligands"] = 1
                    elif case == "ligand_size_record_type":
                        state["items"][0]["size_bytes"] = True
                    elif case == "ligand_size_limit":
                        state["resource_limits"]["max_ligand_bytes"] = (
                            state["items"][0]["size_bytes"] - 1
                        )
                    elif case == "receptor_size_record_type":
                        state["inputs"]["receptor"]["size_bytes"] = "100"
                    elif case == "total_input_limit":
                        total = state["inputs"]["receptor"]["size_bytes"] + sum(
                            item["size_bytes"] for item in state["items"]
                        )
                        state["resource_limits"]["max_total_input_bytes"] = total - 1
                    if case in {
                        "limit_bool",
                        "limit_relaxed_above_hard_max",
                        "item_count",
                        "ligand_size_limit",
                        "total_input_limit",
                    }:
                        state["resource_limits_sha256"] = _resource_limits_hash(
                            state["resource_limits"]
                        )

                self.rewrite_active_state(mutate)
                calls: list[tuple[str, int]] = []
                response = run_screening(
                    str(self.root),
                    runner=_successful_runner(calls),
                )
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "SCREENING_EXECUTION_PREFLIGHT_ERROR",
                )
                self.assertEqual(calls, [])
                persisted = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["status"], "ready")
                self.assertTrue(
                    all(item["attempt_count"] == 0 for item in persisted["items"])
                )
        state_path.write_bytes(original)

    def test_mid_queue_ligand_mutation_blocks_the_next_attempt(self) -> None:
        def mutate(screening):
            target = self.root / screening["items"][1]["ligand_file"]
            target.write_bytes(target.read_bytes() + b"REMARK changed\n")

        self._assert_mid_queue_mutation_blocked(
            mutate,
            "SCREENING_LIGAND_SNAPSHOT_CHANGED",
        )

    def test_mid_queue_receptor_mutation_blocks_the_next_attempt(self) -> None:
        def mutate(screening):
            target = self.root / screening["inputs"]["receptor"]["file"]
            target.write_bytes(target.read_bytes() + b"REMARK changed\n")

        self._assert_mid_queue_mutation_blocked(
            mutate,
            "SCREENING_RECEPTOR_SNAPSHOT_CHANGED",
        )

    def test_mid_queue_vina_mutation_blocks_the_next_attempt(self) -> None:
        def mutate(_screening):
            self.vina.write_bytes(b"changed between ligand attempts")

        self._assert_mid_queue_mutation_blocked(
            mutate,
            "SCREENING_VINA_BINARY_CHANGED",
        )

    def test_attempt_runtime_receptor_mutation_fails_closed_and_resumes(self) -> None:
        def mutate(kwargs):
            receptor = kwargs["cwd"] / "receptor.pdbqt"
            receptor.write_bytes(receptor.read_bytes() + b"REMARK changed\n")

        self._assert_attempt_runtime_mutation_blocked(
            mutate,
            "SCREENING_ATTEMPT_RECEPTOR_CHANGED",
        )

    def test_attempt_runtime_ligand_mutation_fails_closed_and_resumes(self) -> None:
        def mutate(kwargs):
            ligand = kwargs["cwd"] / "ligand.pdbqt"
            ligand.write_bytes(ligand.read_bytes() + b"REMARK changed\n")

        self._assert_attempt_runtime_mutation_blocked(
            mutate,
            "SCREENING_ATTEMPT_LIGAND_CHANGED",
        )

    def test_attempt_runtime_config_mutation_fails_closed_and_resumes(self) -> None:
        def mutate(kwargs):
            config = kwargs["cwd"] / "config.txt"
            config.write_bytes(config.read_bytes() + b"# changed\n")

        self._assert_attempt_runtime_mutation_blocked(
            mutate,
            "SCREENING_ATTEMPT_CONFIG_CHANGED",
        )

    def test_attempt_runtime_vina_mutation_fails_closed_and_resumes(self) -> None:
        original = self.vina.read_bytes()

        def mutate(_kwargs):
            self.vina.write_bytes(b"changed while Vina was running")

        self._assert_attempt_runtime_mutation_blocked(
            mutate,
            "SCREENING_VINA_BINARY_CHANGED",
            restore=lambda: self.vina.write_bytes(original),
        )

    def test_unexpected_run_exception_never_leaves_running_state(self) -> None:
        created = self.create(ligand_files=["prepared/alpha.pdbqt"])
        self.assertTrue(created["ok"], created)
        with patch(
            "dockstart_core.screening._attempt_item",
            side_effect=RuntimeError("unexpected workflow failure"),
        ):
            response = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "SCREENING_RUN_ERROR")
        persisted = json.loads(
            (self.root / "screening" / "screening.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(persisted["status"], "interrupted")
        self.assertEqual(
            persisted["last_runtime_error"]["code"],
            "SCREENING_RUN_ERROR",
        )
        self.assertFalse(
            any(
                item.get("status") == "running"
                for item in persisted["items"]
            )
        )
        resumed = resume_screening(str(self.root))
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["screening"]["queue"], ["ligand_0001"])

    def test_run_recomputes_grid_resource_and_blocks_tampered_unsafe_state(self) -> None:
        created = self.create(ligand_files=["prepared/alpha.pdbqt"])
        self.assertTrue(created["ok"], created)
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["box"].update({"size_x": 126, "size_y": 126, "size_z": 126})
        state["vina"]["spacing"] = 0.1
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        response = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VINA_GRID_RESOURCE_LIMIT_EXCEEDED",
        )
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["status"], "ready")
        self.assertEqual(persisted["items"][0]["attempt_count"], 0)

    def test_run_rechecks_advanced_vina_capabilities(self) -> None:
        created = self.create(
            ligand_files=["prepared/alpha.pdbqt"],
            vina={**VINA, "no_refine": True},
            detection_capabilities=SUPPORTED_ADVANCED_CAPABILITIES,
        )
        self.assertTrue(created["ok"], created)
        detection = SimpleNamespace(
            status="ok",
            path=str(self.vina),
            version="1.2.7",
            source="explicit",
            message="ok",
            raw_error="",
            capabilities={
                "features": {
                    "no_refine": {
                        "status": "unsupported",
                        "supported": False,
                        "message": "missing --no_refine",
                    }
                }
            },
        )
        with patch(
            "dockstart_core.screening.vina_adapter.detect",
            return_value=detection,
        ):
            response = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_VINA_CAPABILITY_MISMATCH",
        )
        self.assertEqual(response["error"]["title"], "当前 AutoDock Vina 不支持批量任务冻结的专家选项。")

    def test_retry_queue_attempt_directories_and_ranked_csv(self) -> None:
        self.assertTrue(self.create()["ok"])
        calls: list[tuple[str, int]] = []
        response = run_screening(
            str(self.root),
            runner=_successful_runner(calls, fail_first_item_once=True),
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["screening"]["status"], "completed")
        self.assertEqual(
            calls,
            [("ligand_0001", 1), ("ligand_0002", 1), ("ligand_0001", 2)],
        )
        self.assertTrue(
            (self.root / "screening" / "attempts" / "ligand_0001" / "attempt_001" / "attempt.json").is_file(),
        )
        self.assertTrue(
            (self.root / "screening" / "attempts" / "ligand_0001" / "attempt_002" / "out.pdbqt").is_file(),
        )
        with (self.root / "screening" / "results" / "screening_summary.csv").open(
            encoding="utf-8",
            newline="",
        ) as handle:
            summary = list(csv.DictReader(handle))
        self.assertEqual(len(summary), 2)
        self.assertEqual(summary[0]["attempts"], "2")
        with (self.root / "screening" / "results" / "screening_top_n.csv").open(
            encoding="utf-8",
            newline="",
        ) as handle:
            top = list(csv.DictReader(handle))
        self.assertEqual([row["rank"] for row in top], ["1", "2"])
        self.assertLessEqual(float(top[0]["best_affinity_kcal_mol"]), float(top[1]["best_affinity_kcal_mol"]))
        self.assertFalse((self.root / "screening" / "results" / "screening.sdf").exists())

    def test_success_records_output_hash_and_size_in_state_attempt_and_csv(self) -> None:
        self.assertTrue(self.create(ligand_files=["prepared/alpha.pdbqt"])["ok"])
        response = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertTrue(response["ok"], response)

        item = response["screening"]["items"][0]
        output_path = self.root / item["best_output_file"]
        expected_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        expected_size = output_path.stat().st_size
        self.assertEqual(item["best_output_sha256"], expected_sha256)
        self.assertEqual(item["best_output_size_bytes"], expected_size)
        self.assertEqual(item["attempts"][-1]["output_sha256"], expected_sha256)
        self.assertEqual(item["attempts"][-1]["output_size_bytes"], expected_size)

        summary_path = (
            self.root / "screening" / "results" / "screening_summary.csv"
        )
        top_path = self.root / "screening" / "results" / "screening_top_n.csv"
        outputs = response["screening"]["outputs"]
        self.assertEqual(
            outputs["summary_sha256"],
            hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(outputs["summary_size_bytes"], summary_path.stat().st_size)
        self.assertEqual(
            outputs["top_n_sha256"],
            hashlib.sha256(top_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(outputs["top_n_size_bytes"], top_path.stat().st_size)

        with summary_path.open(
            encoding="utf-8",
            newline="",
        ) as handle:
            summary = list(csv.DictReader(handle))
        self.assertEqual(summary[0]["best_output_sha256"], expected_sha256)
        self.assertEqual(summary[0]["best_output_size_bytes"], str(expected_size))

    def test_screening_pose_viewer_verifies_frozen_receptor_and_output(self) -> None:
        self.assertTrue(self.create(ligand_files=["prepared/alpha.pdbqt"])["ok"])
        finished = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertTrue(finished["ok"], finished)

        response = load_screening_pose_for_viewer(
            str(self.root),
            "ligand_0001",
            mode=1,
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["integrity"]["status"], "verified")
        self.assertEqual(response["available_modes"], [1])
        self.assertEqual(response["mode"], 1)
        self.assertEqual(response["source_file"], "prepared/alpha.pdbqt")
        self.assertIn("ATOM", response["receptor"]["content"])
        self.assertIn("ATOM", response["pose"]["content"])
        self.assertEqual(
            response["integrity"]["receptor_sha256"],
            response["integrity"]["expected_receptor_sha256"],
        )
        self.assertEqual(
            response["integrity"]["output_sha256"],
            response["integrity"]["expected_output_sha256"],
        )

    def test_screening_pose_viewer_blocks_tampered_output(self) -> None:
        self.assertTrue(self.create(ligand_files=["prepared/alpha.pdbqt"])["ok"])
        finished = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertTrue(finished["ok"], finished)
        item = finished["screening"]["items"][0]
        output_path = self.root / item["best_output_file"]
        output_path.write_text(
            output_path.read_text(encoding="utf-8") + "REMARK tampered\n",
            encoding="utf-8",
        )

        response = load_screening_pose_for_viewer(str(self.root), "ligand_0001", mode=1)
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VIEWER_SCREENING_OUTPUT_HASH_MISMATCH",
        )

    def test_screening_pose_viewer_blocks_item_attempt_hash_conflict(self) -> None:
        self.assertTrue(self.create(ligand_files=["prepared/alpha.pdbqt"])["ok"])
        finished = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertTrue(finished["ok"], finished)
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["items"][0]["best_output_sha256"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")

        response = load_screening_pose_for_viewer(str(self.root), "ligand_0001", mode=1)
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VIEWER_SCREENING_OUTPUT_HASH_CONFLICT",
        )

    def test_screening_pose_viewer_rejects_output_outside_item_attempt(self) -> None:
        self.assertTrue(self.create(ligand_files=["prepared/alpha.pdbqt"])["ok"])
        finished = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertTrue(finished["ok"], finished)
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["items"][0]["best_output_file"] = "prepared/alpha.pdbqt"
        state["items"][0]["attempts"][-1]["directory"] = "prepared"
        state["items"][0]["attempts"][-1]["output_file"] = "prepared/alpha.pdbqt"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        response = load_screening_pose_for_viewer(str(self.root), "ligand_0001", mode=1)
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VIEWER_SCREENING_OUTPUT_SCOPE_INVALID",
        )

    def test_screening_pose_viewer_marks_old_hashless_record_unverified(self) -> None:
        self.assertTrue(self.create(ligand_files=["prepared/alpha.pdbqt"])["ok"])
        finished = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertTrue(finished["ok"], finished)
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["items"][0].pop("best_output_sha256")
        state["items"][0]["attempts"][-1].pop("output_sha256")
        state_path.write_text(json.dumps(state), encoding="utf-8")

        response = load_screening_pose_for_viewer(str(self.root), "ligand_0001", mode=1)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["integrity"]["status"], "legacy_unverified")
        self.assertEqual(response["integrity"]["expected_output_sha256"], "")
        self.assertTrue(response["warnings"])
        self.assertIn("没有保存完整输出 SHA256", response["warnings"][0])

    def test_report_refuses_nonterminal_screening(self) -> None:
        self.assertTrue(self.create()["ok"])
        response = export_screening_markdown_report(str(self.root))
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_REPORT_NOT_TERMINAL",
        )
        self.assertFalse(
            (self.root / "screening" / "results" / "screening_report.md").exists(),
        )

    def test_completed_screening_report_is_hashed_and_recorded_in_state(self) -> None:
        self.assertTrue(self.create()["ok"])
        finished = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertTrue(finished["ok"], finished)

        response = export_screening_markdown_report(str(self.root))
        self.assertTrue(response["ok"], response)
        self.assertEqual(
            response["report_file"],
            "screening/results/screening_report.md",
        )
        report_path = self.root / response["report_file"]
        expected_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
        self.assertEqual(response["report_sha256"], expected_sha256)
        self.assertEqual(response["report_size_bytes"], report_path.stat().st_size)
        content = report_path.read_text(encoding="utf-8")
        self.assertIn("# DockStart 批量筛选实验记录", content)
        self.assertIn("## Top 2", content)
        self.assertIn("## 完整结果", content)
        self.assertIn("Docking score 仅供结构结合趋势参考，不能替代实验验证。", content)
        self.assertIn("prepared/alpha.pdbqt", content)
        self.assertIn("prepared/zeta.pdbqt", content)
        self.assertIn("Vina 自动决定", content)

        state = json.loads(
            (self.root / "screening" / "screening.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(state["outputs"]["report_md"], response["report_file"])
        self.assertEqual(state["outputs"]["report_sha256"], expected_sha256)
        self.assertEqual(
            state["outputs"]["report_size_bytes"],
            report_path.stat().st_size,
        )
        self.assertTrue(state["outputs"]["reported_at"])

    def test_cli_report_prints_one_json_document(self) -> None:
        self.assertTrue(self.create()["ok"])
        self.assertTrue(run_screening(str(self.root), runner=_successful_runner([]))["ok"])
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["report", "--project", str(self.root)])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(
            payload["report_file"],
            "screening/results/screening_report.md",
        )
        self.assertTrue((self.root / payload["report_file"]).is_file())

    def test_cancel_after_active_ligand_then_resume_remaining_queue(self) -> None:
        self.assertTrue(self.create()["ok"])
        calls: list[tuple[str, int]] = []
        base_runner = _successful_runner(calls)

        def canceling_runner(**kwargs):
            result = base_runner(**kwargs)
            canceled = request_screening_cancel(str(self.root))
            self.assertTrue(canceled["ok"])
            return result

        canceled = run_screening(str(self.root), runner=canceling_runner)
        self.assertTrue(canceled["ok"])
        self.assertEqual(canceled["screening"]["status"], "canceled")
        self.assertEqual(calls, [("ligand_0001", 1)])
        self.assertEqual(canceled["screening"]["queue"], ["ligand_0002"])

        resumed = resume_screening(str(self.root))
        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["screening"]["status"], "ready")
        calls.clear()
        finished = run_screening(str(self.root), runner=_successful_runner(calls))
        self.assertEqual(finished["screening"]["status"], "completed")
        self.assertEqual(calls, [("ligand_0002", 1)])

    def test_archive_terminal_job_clears_active_state_and_preserves_history(self) -> None:
        self.assertTrue(self.create()["ok"])
        finished = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertEqual(finished["screening"]["status"], "completed")
        archived = archive_screening(str(self.root))
        self.assertTrue(archived["ok"], archived)
        archive_dir = self.root / archived["archive"]
        self.assertTrue((archive_dir / "screening.json").is_file())
        self.assertTrue((archive_dir / "results" / "screening_summary.csv").is_file())
        self.assertFalse((self.root / "screening" / "screening.json").exists())
        self.assertFalse((self.root / "screening" / "inputs").exists())

        next_job = self.create(ligand_files=["prepared/alpha.pdbqt"])
        self.assertTrue(next_job["ok"], next_job)
        self.assertEqual(next_job["screening"]["screening_id"], "screening_002")

    def test_archive_generates_report_and_freezes_staged_labels(self) -> None:
        with tempfile.TemporaryDirectory() as external_dir:
            source = Path(external_dir) / "候选 配体.pdbqt"
            source.write_text(_pdbqt("S"), encoding="utf-8")
            staged = stage_screening_inputs(str(self.root), [str(source)])
        self.assertTrue(staged["ok"], staged)
        archived = self.create_completed_archive(
            ligand_files=[staged["staged"][0]["file"]],
        )
        archive_dir = self.root / archived["archive"]
        report_path = archive_dir / "results" / "screening_report.md"
        self.assertTrue(report_path.is_file())
        manifest = json.loads(
            (archive_dir / "archive_manifest.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(manifest["item_labels"]["ligand_0001"], "候选 配体.pdbqt")
        state = json.loads((archive_dir / "screening.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["outputs"]["report_md"],
            "screening/results/screening_report.md",
        )
        self.assertEqual(
            hashlib.sha256((archive_dir / "screening.json").read_bytes()).hexdigest(),
            manifest["state_sha256"],
        )
        self.assertTrue((self.root / "screening" / "staging" / "index.json").is_file())

        detail = get_screening_archive(str(self.root), archived["archive_id"])
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["item_labels"]["ligand_0001"], "候选 配体.pdbqt")
        self.assertEqual(detail["staged"][0]["original_name"], "候选 配体.pdbqt")
        archive_prefix = f"screening/archive/{archived['archive_id']}/"
        self.assertTrue(detail["files"]["receptor"].startswith(archive_prefix))
        self.assertTrue(detail["files"]["report_md"].startswith(archive_prefix))
        self.assertTrue(
            detail["files"]["items"]["ligand_0001"]["best_output"].startswith(
                archive_prefix,
            )
        )
        self.assertEqual(detail["attempt_integrity"]["status"], "verified")
        self.assertEqual(detail["archive"]["attempt_integrity"], "verified")

    def test_archive_detail_and_comparison_reject_tampered_attempt_evidence(self) -> None:
        tampered = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        reference = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        archive_dir = self.root / tampered["archive"]
        config_path = (
            archive_dir
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "config.txt"
        )
        config_path.write_bytes(config_path.read_bytes() + b"# tampered\n")

        detail = get_screening_archive(str(self.root), tampered["archive_id"])
        self.assertFalse(detail["ok"])
        self.assertEqual(
            detail["error"]["code"],
            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_HASH_MISMATCH",
        )
        compared = compare_screening_archives(
            str(self.root),
            [tampered["archive_id"], reference["archive_id"]],
        )
        self.assertFalse(compared["ok"])
        self.assertEqual(
            compared["error"]["code"],
            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_HASH_MISMATCH",
        )

    def test_archive_detail_keeps_legacy_attempt_without_evidence_readable(self) -> None:
        archived = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        archive_dir = self.root / archived["archive"]

        def remove_attempt_evidence(state):
            attempt = state["items"][0]["attempts"][0]
            for key in (
                "input_snapshots",
                "config_snapshot",
                "vina_snapshot",
                "integrity",
            ):
                attempt.pop(key, None)

        self.rewrite_archived_state(archive_dir, remove_attempt_evidence)
        detail = get_screening_archive(str(self.root), archived["archive_id"])
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(
            detail["attempt_integrity"]["status"],
            "legacy_unverified",
        )
        self.assertTrue(detail["attempt_integrity"]["warnings"])

    def test_archive_attempt_postrun_files_must_match_pre_run_and_archive(self) -> None:
        candidate = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        reference = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        archive_dir = self.root / candidate["archive"]
        state_path = archive_dir / "screening.json"
        manifest_path = archive_dir / "archive_manifest.json"
        original_state = state_path.read_bytes()
        original_manifest = manifest_path.read_bytes()
        first = True
        for evidence_name in ("receptor", "ligand", "config", "vina"):
            for field in ("sha256", "size_bytes"):
                with self.subTest(evidence=evidence_name, field=field):
                    state_path.write_bytes(original_state)
                    manifest_path.write_bytes(original_manifest)

                    def mutate(
                        state,
                        evidence_name=evidence_name,
                        field=field,
                    ):
                        runtime = state["items"][0]["attempts"][0][
                            "integrity"
                        ]["files"][evidence_name]
                        runtime[field] = (
                            "0" * 64
                            if field == "sha256"
                            else runtime[field] + 1
                        )

                    self.rewrite_archived_state(archive_dir, mutate)
                    detail = get_screening_archive(
                        str(self.root),
                        candidate["archive_id"],
                    )
                    self.assertFalse(detail["ok"])
                    self.assertEqual(
                        detail["error"]["code"],
                        "SCREENING_ARCHIVE_ATTEMPT_POSTRUN_MISMATCH",
                    )
                    if first:
                        compared = compare_screening_archives(
                            str(self.root),
                            [candidate["archive_id"], reference["archive_id"]],
                        )
                        self.assertFalse(compared["ok"])
                        self.assertEqual(
                            compared["error"]["code"],
                            "SCREENING_ARCHIVE_ATTEMPT_POSTRUN_MISMATCH",
                        )
                        first = False
        state_path.write_bytes(original_state)
        manifest_path.write_bytes(original_manifest)

    def test_archive_modern_missing_or_failed_integrity_is_partial_not_legacy(self) -> None:
        archived = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        reference = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        archive_dir = self.root / archived["archive"]
        state_path = archive_dir / "screening.json"
        manifest_path = archive_dir / "archive_manifest.json"
        original_state = state_path.read_bytes()
        original_manifest = manifest_path.read_bytes()
        cases = (
            "missing_pre_config",
            "missing_file",
            "missing_integrity",
            "failed",
            "not_checked",
        )
        for case in cases:
            with self.subTest(case=case):
                state_path.write_bytes(original_state)
                manifest_path.write_bytes(original_manifest)

                def mutate(state, case=case):
                    attempt = state["items"][0]["attempts"][0]
                    if case == "missing_pre_config":
                        attempt.pop("config_snapshot")
                    elif case == "missing_file":
                        attempt["integrity"]["files"].pop("config")
                    elif case == "missing_integrity":
                        attempt.pop("integrity")
                    else:
                        attempt["integrity"]["status"] = case

                self.rewrite_archived_state(archive_dir, mutate)
                detail = get_screening_archive(
                    str(self.root),
                    archived["archive_id"],
                )
                self.assertTrue(detail["ok"], detail)
                integrity = detail["attempt_integrity"]
                self.assertEqual(integrity["status"], "partially_verified")
                self.assertEqual(
                    integrity["counts"]["partially_verified"],
                    1,
                )
                self.assertEqual(
                    integrity["counts"]["legacy_unverified"],
                    0,
                )
                if case == "missing_pre_config":
                    compared = compare_screening_archives(
                        str(self.root),
                        [archived["archive_id"], reference["archive_id"]],
                    )
                    self.assertTrue(compared["ok"], compared)
                    self.assertEqual(
                        compared["baseline_archive"]["attempt_integrity"],
                        "partially_verified",
                    )
        state_path.write_bytes(original_state)
        manifest_path.write_bytes(original_manifest)

    def test_archive_attempt_integrity_mixed_legacy_and_verified_is_partial(self) -> None:
        archived = self.create_completed_archive()
        archive_dir = self.root / archived["archive"]

        def remove_second_attempt_evidence(state):
            attempt = state["items"][1]["attempts"][0]
            for key in (
                "input_snapshots",
                "config_snapshot",
                "vina_snapshot",
                "integrity",
            ):
                attempt.pop(key, None)

        self.rewrite_archived_state(archive_dir, remove_second_attempt_evidence)
        detail = get_screening_archive(str(self.root), archived["archive_id"])
        self.assertTrue(detail["ok"], detail)
        integrity = detail["attempt_integrity"]
        self.assertEqual(integrity["status"], "partially_verified")
        self.assertEqual(integrity["counts"]["verified"], 1)
        self.assertEqual(integrity["counts"]["legacy_unverified"], 1)

    def test_archive_failed_integrity_with_contradictory_fields_is_invalid(self) -> None:
        archived = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        archive_dir = self.root / archived["archive"]

        def contradict(state):
            integrity = state["items"][0]["attempts"][0]["integrity"]
            integrity["status"] = "failed"
            integrity["files"]["vina"]["size_bytes"] += 1

        self.rewrite_archived_state(archive_dir, contradict)
        detail = get_screening_archive(str(self.root), archived["archive_id"])
        self.assertFalse(detail["ok"])
        self.assertEqual(
            detail["error"]["code"],
            "SCREENING_ARCHIVE_ATTEMPT_POSTRUN_MISMATCH",
        )

    def test_archive_detail_and_compare_revalidate_complete_resource_policy(self) -> None:
        candidate = self.create_completed_archive()
        reference = self.create_completed_archive()
        archive_dir = self.root / candidate["archive"]
        state_path = archive_dir / "screening.json"
        manifest_path = archive_dir / "archive_manifest.json"
        original_state = state_path.read_bytes()
        original_manifest = manifest_path.read_bytes()
        cases = (
            "limit_bool",
            "limit_relaxed_above_hard_max",
            "limit_hash_mismatch",
            "max_retries_bool",
            "max_retries_above_limit",
            "top_n_above_limit",
            "item_count",
            "ligand_size_limit",
            "total_input_limit",
        )
        for case in cases:
            with self.subTest(case=case):
                state_path.write_bytes(original_state)
                manifest_path.write_bytes(original_manifest)

                def mutate(state, case=case):
                    if case == "limit_bool":
                        state["resource_limits"]["max_staged_file_bytes"] = True
                    elif case == "limit_relaxed_above_hard_max":
                        state["resource_limits"]["max_total_input_bytes"] = (
                            512 * 1024 * 1024 + 1
                        )
                    elif case == "limit_hash_mismatch":
                        state["resource_limits"]["max_ligands"] = 499
                    elif case == "max_retries_bool":
                        state["max_retries"] = True
                    elif case == "max_retries_above_limit":
                        state["resource_limits"]["max_retries"] = 0
                    elif case == "top_n_above_limit":
                        state["top_n"] = (
                            state["resource_limits"]["max_ligands"] + 1
                        )
                    elif case == "item_count":
                        state["resource_limits"]["max_ligands"] = 1
                    elif case == "ligand_size_limit":
                        state["resource_limits"]["max_ligand_bytes"] = (
                            state["items"][0]["size_bytes"] - 1
                        )
                    elif case == "total_input_limit":
                        total = state["inputs"]["receptor"]["size_bytes"] + sum(
                            item["size_bytes"] for item in state["items"]
                        )
                        state["resource_limits"]["max_total_input_bytes"] = total - 1
                    if case in {
                        "limit_bool",
                        "limit_relaxed_above_hard_max",
                        "max_retries_above_limit",
                        "item_count",
                        "ligand_size_limit",
                        "total_input_limit",
                    }:
                        state["resource_limits_sha256"] = _resource_limits_hash(
                            state["resource_limits"]
                        )

                self.rewrite_archived_state(archive_dir, mutate)
                detail = get_screening_archive(
                    str(self.root),
                    candidate["archive_id"],
                )
                self.assertFalse(detail["ok"])
                self.assertEqual(
                    detail["error"]["code"],
                    "SCREENING_ARCHIVE_RESOURCE_POLICY_INVALID",
                )
                compared = compare_screening_archives(
                    str(self.root),
                    [candidate["archive_id"], reference["archive_id"]],
                )
                self.assertFalse(compared["ok"])
                self.assertEqual(
                    compared["error"]["code"],
                    "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                )
        state_path.write_bytes(original_state)
        manifest_path.write_bytes(original_manifest)

    def test_archive_missing_resource_limits_uses_explicit_default_inference(self) -> None:
        legacy = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        reference = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        archive_dir = self.root / legacy["archive"]
        self.rewrite_archived_state(
            archive_dir,
            lambda state: state.pop("resource_limits", None),
        )

        detail = get_screening_archive(str(self.root), legacy["archive_id"])
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["resource_integrity"]["status"], "verified")
        self.assertEqual(
            detail["screening"]["resource_limits"]["max_ligands"],
            500,
        )
        self.assertEqual(
            set(
                detail["screening"]["compatibility"][
                    "inferred_resource_limit_fields"
                ]
            ),
            {
                "max_ligands",
                "max_retries",
                "max_cpu",
                "max_exhaustiveness",
                "max_num_modes",
                "max_box_edge_angstrom",
                "max_ligand_bytes",
                "max_staged_file_bytes",
                "max_total_input_bytes",
            },
        )
        compared = compare_screening_archives(
            str(self.root),
            [legacy["archive_id"], reference["archive_id"]],
        )
        self.assertTrue(compared["ok"], compared)
        self.assertTrue(compared["direct_score_comparison"])

    def test_compare_includes_all_valid_resource_limits_in_protocol_fingerprint(self) -> None:
        baseline = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        comparison = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        archive_dir = self.root / comparison["archive"]

        def lower_safe_limit(state):
            state["resource_limits"]["max_ligands"] = 499
            state["resource_limits_sha256"] = _resource_limits_hash(
                state["resource_limits"]
            )

        self.rewrite_archived_state(archive_dir, lower_safe_limit)
        detail = get_screening_archive(str(self.root), comparison["archive_id"])
        self.assertTrue(detail["ok"], detail)
        compared = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )
        self.assertTrue(compared["ok"], compared)
        self.assertFalse(compared["direct_score_comparison"])
        self.assertEqual(
            [
                difference["field"]
                for difference in compared["comparability"]["differences"]
            ],
            ["resource_limits.max_ligands"],
        )

    def test_archive_list_keeps_damaged_entries_visible(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        damaged_id = "screening_999_20260101000000"
        (self.root / "screening" / "archive" / damaged_id).mkdir()

        response = list_screening_archives(str(self.root))
        self.assertTrue(response["ok"], response)
        by_id = {entry["archive_id"]: entry for entry in response["archives"]}
        self.assertTrue(by_id[archived["archive_id"]]["valid"])
        self.assertEqual(
            by_id[archived["archive_id"]]["integrity"],
            "state_verified",
        )
        self.assertTrue(by_id[archived["archive_id"]]["report_available"])
        self.assertFalse(by_id[damaged_id]["valid"])
        self.assertEqual(by_id[damaged_id]["integrity"], "invalid")
        self.assertEqual(
            by_id[damaged_id]["error"]["code"],
            "SCREENING_ARCHIVE_METADATA_MISSING",
        )

    def test_archive_detail_rejects_unsafe_id_and_state_hash_mismatch(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        unsafe = get_screening_archive(str(self.root), "../screening.json")
        self.assertFalse(unsafe["ok"])
        self.assertEqual(unsafe["error"]["code"], "SCREENING_ARCHIVE_ID_INVALID")

        archive_dir = self.root / archived["archive"]
        state_path = archive_dir / "screening.json"
        state_path.write_text(
            state_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        mismatch = get_screening_archive(str(self.root), archived["archive_id"])
        self.assertFalse(mismatch["ok"])
        self.assertEqual(
            mismatch["error"]["code"],
            "SCREENING_ARCHIVE_STATE_HASH_MISMATCH",
        )

    def test_archive_manifest_schema_id_and_status_are_strict(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        manifest_path = self.root / archived["archive"] / "archive_manifest.json"
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        cases = (
            (
                {"schema_version": 2},
                "SCREENING_ARCHIVE_MANIFEST_UNSUPPORTED",
            ),
            (
                {"screening_id": "screening_999"},
                "SCREENING_ARCHIVE_ID_MISMATCH",
            ),
            (
                {"status": "canceled"},
                "SCREENING_ARCHIVE_STATUS_MISMATCH",
            ),
        )
        for updates, expected_code in cases:
            with self.subTest(updates=updates):
                changed = {**original, **updates}
                manifest_path.write_text(json.dumps(changed), encoding="utf-8")
                response = get_screening_archive(
                    str(self.root),
                    archived["archive_id"],
                )
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], expected_code)
        manifest_path.write_text(json.dumps(original), encoding="utf-8")

    def test_archive_detail_normalizes_legacy_v1_without_writing_it(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]

        def make_legacy(state):
            outputs = state["outputs"]
            outputs.pop("report_md")
            outputs.pop("report_sha256")
            outputs.pop("reported_at")
            item = state["items"][0]
            item.pop("best_output_sha256")
            item.pop("best_output_size_bytes")
            item["attempts"][0].pop("output_sha256")
            item["attempts"][0].pop("output_size_bytes")

        self.rewrite_archived_state(archive_dir, make_legacy)
        manifest_path = archive_dir / "archive_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("item_labels")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        state_path = archive_dir / "screening.json"
        state_before = state_path.read_bytes()
        manifest_before = manifest_path.read_bytes()

        detail = get_screening_archive(str(self.root), archived["archive_id"])
        self.assertTrue(detail["ok"], detail)
        item = detail["screening"]["items"][0]
        self.assertEqual(item["best_output_sha256"], "")
        self.assertEqual(item["best_output_size_bytes"], 0)
        self.assertEqual(item["attempts"][0]["output_sha256"], "")
        self.assertEqual(detail["screening"]["outputs"]["report_md"], "")
        self.assertEqual(detail["files"]["report_md"], "")
        self.assertEqual(detail["item_labels"]["ligand_0001"], "alpha.pdbqt")
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertEqual(manifest_path.read_bytes(), manifest_before)

    def test_archived_viewer_uses_selected_archive_not_new_active_job(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        next_job = self.create(ligand_files=["prepared/zeta.pdbqt"])
        self.assertTrue(next_job["ok"], next_job)

        response = load_archived_screening_pose_for_viewer(
            str(self.root),
            archived["archive_id"],
            "ligand_0001",
            mode=1,
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["integrity"]["status"], "verified")
        self.assertEqual(response["display_label"], "alpha.pdbqt")
        self.assertEqual(response["source_file"], "prepared/alpha.pdbqt")
        self.assertIn(
            f"screening/archive/{archived['archive_id']}/",
            response["pose"]["relative_path"],
        )

    def test_archived_viewer_rejects_mode_above_fifty(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        response = load_archived_screening_pose_for_viewer(
            str(self.root),
            archived["archive_id"],
            "ligand_0001",
            mode=51,
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VIEWER_SCREENING_MODE_INVALID",
        )

    def test_archived_viewer_blocks_output_tampering(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        state = json.loads((archive_dir / "screening.json").read_text(encoding="utf-8"))
        output_parts = state["items"][0]["best_output_file"].split("/")
        output_path = archive_dir.joinpath(*output_parts[1:])
        original = output_path.read_bytes()
        output_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])

        response = load_archived_screening_pose_for_viewer(
            str(self.root),
            archived["archive_id"],
            "ligand_0001",
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VIEWER_SCREENING_OUTPUT_HASH_MISMATCH",
        )

    def test_archived_viewer_blocks_hash_conflict_and_size_mismatch(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]

        def conflict(state):
            state["items"][0]["best_output_sha256"] = "0" * 64

        self.rewrite_archived_state(archive_dir, conflict)
        response = load_archived_screening_pose_for_viewer(
            str(self.root),
            archived["archive_id"],
            "ligand_0001",
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VIEWER_SCREENING_OUTPUT_HASH_CONFLICT",
        )

        state = json.loads((archive_dir / "screening.json").read_text(encoding="utf-8"))
        actual_hash = state["items"][0]["attempts"][0]["output_sha256"]

        def size_mismatch(updated):
            item = updated["items"][0]
            item["best_output_sha256"] = actual_hash
            item["best_output_size_bytes"] += 1
            item["attempts"][0]["output_size_bytes"] = item["best_output_size_bytes"]

        self.rewrite_archived_state(archive_dir, size_mismatch)
        response = load_archived_screening_pose_for_viewer(
            str(self.root),
            archived["archive_id"],
            "ligand_0001",
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "VIEWER_SCREENING_OUTPUT_SIZE_MISMATCH",
        )

    def test_archived_viewer_supports_legacy_hashless_output(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]

        def make_legacy(state):
            item = state["items"][0]
            item.pop("best_output_sha256")
            item.pop("best_output_size_bytes")
            item["attempts"][0].pop("output_sha256")
            item["attempts"][0].pop("output_size_bytes")

        self.rewrite_archived_state(archive_dir, make_legacy)
        response = load_archived_screening_pose_for_viewer(
            str(self.root),
            archived["archive_id"],
            "ligand_0001",
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["integrity"]["status"], "legacy_unverified")
        self.assertTrue(response["warnings"])

    def test_archived_viewer_enforces_twenty_megabyte_limit(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        state_path = archive_dir / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        output_parts = state["items"][0]["best_output_file"].split("/")
        output_path = archive_dir.joinpath(*output_parts[1:])
        output_path.write_bytes(b"ATOM  " + b"X" * (20 * 1024 * 1024))
        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        size = output_path.stat().st_size

        def update_output(updated):
            item = updated["items"][0]
            item["best_output_sha256"] = digest
            item["best_output_size_bytes"] = size
            item["attempts"][0]["output_sha256"] = digest
            item["attempts"][0]["output_size_bytes"] = size

        self.rewrite_archived_state(archive_dir, update_output)
        response = load_archived_screening_pose_for_viewer(
            str(self.root),
            archived["archive_id"],
            "ligand_0001",
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "VIEWER_FILE_TOO_LARGE")

    def test_archive_detail_rejects_relocated_output_escape(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]

        def escape(state):
            escaped = "screening/attempts/ligand_0001/../../inputs/receptor.pdbqt"
            state["items"][0]["best_output_file"] = escaped
            state["items"][0]["attempts"][0]["output_file"] = escaped

        self.rewrite_archived_state(archive_dir, escape)
        response = get_screening_archive(str(self.root), archived["archive_id"])
        self.assertFalse(response["ok"])
        self.assertIn(
            response["error"]["code"],
            {
                "SCREENING_ARCHIVE_OUTPUT_RECORD_INVALID",
                "SCREENING_ARCHIVE_FILE_PATH_INVALID",
            },
        )

    def test_archive_list_detail_and_viewer_cli_print_one_json_document(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["archives", "--project", str(self.root)])
        self.assertEqual(exit_code, 0)
        listed = json.loads(output.getvalue())
        self.assertEqual(listed["archives"][0]["archive_id"], archived["archive_id"])

        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "archive-detail",
                    "--project",
                    str(self.root),
                    "--archive-id",
                    archived["archive_id"],
                ]
            )
        self.assertEqual(exit_code, 0)
        detail = json.loads(output.getvalue())
        self.assertTrue(detail["ok"], detail)

        output = StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "viewer.py",
                    "load-screening-archive-pose",
                    str(self.root),
                    archived["archive_id"],
                    "ligand_0001",
                    "1",
                ],
            ),
            redirect_stdout(output),
        ):
            viewer_main()
        viewed = json.loads(output.getvalue())
        self.assertTrue(viewed["ok"], viewed)
        self.assertEqual(viewed["archive_id"], archived["archive_id"])

    def test_archive_zip_export_is_deterministic_canonical_and_self_verifying(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        first_path = self.root / "screening-export-one.zip"
        second_path = self.root / "screening-export-two.zip"

        first = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(first_path),
        )
        second = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(second_path),
        )

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
        self.assertEqual(first["zip_sha256"], second["zip_sha256"])
        self.assertEqual(
            first["zip_sha256"],
            hashlib.sha256(first_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(first["size_bytes"], first_path.stat().st_size)
        self.assertFalse(first["overwritten"])
        self.assertRegex(first["exported_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(
            first["source_integrity"],
            {
                "bundle": "verified",
                "resource": "verified",
                "attempt": "verified",
                "output": "verified",
            },
        )
        self.assertEqual(len(first["warnings"]), 2)
        self.assertTrue(any("脱敏" in value for value in first["warnings"]))
        self.assertTrue(any("数字签名" in value for value in first["warnings"]))

        with zipfile.ZipFile(first_path, "r") as bundle:
            infos = bundle.infolist()
            names = [info.filename for info in infos]
            self.assertEqual(names[0], "dockstart_screening_export.json")
            self.assertEqual(first["entry_count"], len(names))
            self.assertEqual(len(names), len({name.casefold() for name in names}))
            self.assertFalse(any(info.is_dir() for info in infos))
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos))
            self.assertTrue(all(info.create_system == 3 for info in infos))
            manifest_bytes = bundle.read("dockstart_screening_export.json")
            manifest = json.loads(manifest_bytes)
            canonical = (
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            self.assertEqual(manifest_bytes, canonical)
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(
                manifest["artifact_type"],
                "dockstart_screening_archive_zip",
            )
            self.assertEqual(manifest["archive_id"], archived["archive_id"])
            payload = manifest["payload"]
            self.assertEqual(payload["file_count"], len(names) - 1)
            self.assertEqual(
                first["payload_uncompressed_bytes"],
                payload["uncompressed_bytes"],
            )
            tree_bytes = (
                json.dumps(
                    payload["files"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            self.assertEqual(
                payload["tree_sha256"],
                hashlib.sha256(tree_bytes).hexdigest(),
            )
            self.assertEqual(
                first["payload_tree_sha256"],
                payload["tree_sha256"],
            )
            for record in payload["files"]:
                payload_bytes = bundle.read(record["zip_path"])
                self.assertEqual(len(payload_bytes), record["size_bytes"])
                self.assertEqual(
                    hashlib.sha256(payload_bytes).hexdigest(),
                    record["sha256"],
                )
            payload_names = names[1:]
            self.assertFalse(any("/raw/" in f"/{name}/" for name in payload_names))
            self.assertFalse(any("/staging/" in f"/{name}/" for name in payload_names))
            self.assertFalse(any(name.endswith("/project.json") for name in payload_names))
            self.assertFalse(any(name.lower().endswith("/vina.exe") for name in payload_names))

    def test_archive_zip_export_preserves_legacy_integrity_warnings(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]

        def make_legacy(state):
            state.pop("resource_limits", None)
            state.pop("resource_limits_sha256", None)
            for key in (
                "max_evals",
                "min_rmsd",
                "spacing",
                "verbosity",
                "no_refine",
                "force_even_voxels",
            ):
                state["vina"].pop(key, None)
            attempt = state["items"][0]["attempts"][0]
            for key in (
                "input_snapshots",
                "config_snapshot",
                "vina_snapshot",
                "integrity",
            ):
                attempt.pop(key, None)

        self.rewrite_archived_state(archive_dir, make_legacy)
        (
            archive_dir
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "attempt.json"
        ).unlink()
        destination = self.root / "legacy.zip"
        response = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(destination),
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["source_integrity"]["resource"], "legacy_unverified")
        self.assertEqual(response["source_integrity"]["attempt"], "legacy_unverified")
        self.assertTrue(response["warnings"])
        with zipfile.ZipFile(destination, "r") as bundle:
            manifest = json.loads(bundle.read("dockstart_screening_export.json"))
        self.assertEqual(
            manifest["source_integrity"],
            response["source_integrity"],
        )
        self.assertEqual(manifest["warnings"], response["warnings"])

    def test_archive_zip_export_allows_partially_verified_attempt_with_warning(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]

        def make_partial(state):
            state["items"][0]["attempts"][0].pop("config_snapshot")

        state = self.rewrite_archived_state(archive_dir, make_partial)
        (
            archive_dir
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "attempt.json"
        ).write_text(
            json.dumps(
                state["items"][0]["attempts"][0],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        response = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(self.root / "partial.zip"),
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(
            response["source_integrity"]["attempt"],
            "partially_verified",
        )
        self.assertTrue(
            any("部分证据" in warning for warning in response["warnings"])
        )

    def test_archive_zip_export_binds_attempt_json_semantics(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        attempt_path = (
            archive_dir
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "attempt.json"
        )
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        reordered = dict(reversed(list(attempt.items())))
        attempt_path.write_text(
            json.dumps(reordered, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
        equivalent = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(self.root / "attempt-equivalent.zip"),
        )
        self.assertTrue(equivalent["ok"], equivalent)
        self.assertEqual(
            equivalent["source_integrity"]["attempt"],
            "verified",
        )

        attempt["exit_code"] = False
        attempt_path.write_text(
            json.dumps(attempt, ensure_ascii=False),
            encoding="utf-8",
        )
        mismatched = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(self.root / "attempt-type-mismatch.zip"),
        )
        self.assertFalse(mismatched["ok"])
        self.assertEqual(
            mismatched["error"]["code"],
            "SCREENING_ARCHIVE_ATTEMPT_JSON_MISMATCH",
        )

    def test_archive_zip_export_rejects_ambiguous_attempt_json(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        attempt_path = (
            archive_dir
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "attempt.json"
        )
        cases = (
            '{"attempt":1,"attempt":1}',
            '["not-an-object"]',
            '{"overflow":1e9999}',
            '{"nested":{"value":1,"value":1}}',
        )
        for index, payload in enumerate(cases, start=1):
            with self.subTest(payload=payload):
                attempt_path.write_text(payload, encoding="utf-8")
                destination = self.root / f"ambiguous-attempt-{index}.zip"
                response = export_screening_archive_zip(
                    str(self.root),
                    archived["archive_id"],
                    str(destination),
                )
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                )
                self.assertFalse(destination.exists())

    def test_archive_zip_export_missing_attempt_json_is_legacy_partial(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        (
            archive_dir
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "attempt.json"
        ).unlink()
        response = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(self.root / "missing-attempt-json.zip"),
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(
            response["source_integrity"]["attempt"],
            "partially_verified",
        )
        self.assertTrue(
            any("缺少 attempt.json" in warning for warning in response["warnings"])
        )

    def test_archive_zip_export_enforces_attempt_json_semantic_size_limit(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        destination = self.root / "attempt-json-limit.zip"
        with patch(
            "dockstart_core.screening.MAX_SCREENING_EXPORT_SEMANTIC_JSON_BYTES",
            16,
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
        )
        self.assertFalse(destination.exists())

    def test_archive_zip_export_result_artifacts_use_historical_evidence(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        cases = (
            ("screening_summary.csv", "summary"),
            ("screening_top_n.csv", "top"),
            ("screening_report.md", "report"),
        )
        for filename, label in cases:
            path = archive_dir / "results" / filename
            original = path.read_bytes()
            path.write_bytes(original + b"\nTAMPERED")
            destination = self.root / f"tampered-{label}-result.zip"
            try:
                response = export_screening_archive_zip(
                    str(self.root),
                    archived["archive_id"],
                    str(destination),
                )
            finally:
                path.write_bytes(original)
            self.assertFalse(response["ok"])
            self.assertIn(
                response["error"]["code"],
                {
                    "SCREENING_ARCHIVE_EXPORT_OUTPUT_HASH_MISMATCH",
                    "SCREENING_ARCHIVE_EXPORT_OUTPUT_SIZE_MISMATCH",
                    "SCREENING_ARCHIVE_FILE_HASH_MISMATCH",
                },
            )
            self.assertFalse(destination.exists())

    def test_archive_zip_export_legacy_result_evidence_is_partial(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]

        def remove_legacy_evidence(state):
            outputs = state["outputs"]
            for key in (
                "summary_sha256",
                "summary_size_bytes",
                "top_n_sha256",
                "top_n_size_bytes",
                "report_size_bytes",
            ):
                outputs.pop(key, None)

        self.rewrite_archived_state(archive_dir, remove_legacy_evidence)
        response = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(self.root / "legacy-result-evidence.zip"),
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(
            response["source_integrity"]["output"],
            "partially_verified",
        )
        self.assertTrue(
            any("完整汇总 CSV" in warning for warning in response["warnings"])
        )
        self.assertTrue(
            any("Markdown 实验记录" in warning for warning in response["warnings"])
        )

    def test_archive_zip_export_rejects_tampered_source_and_unknown_entries(self) -> None:
        tampered = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        tampered_dir = self.root / tampered["archive"]
        ligand = tampered_dir / "inputs" / "ligands" / "ligand_0001.pdbqt"
        ligand.write_bytes(ligand.read_bytes() + b"\n")
        tampered_destination = self.root / "tampered.zip"
        response = export_screening_archive_zip(
            str(self.root),
            tampered["archive_id"],
            str(tampered_destination),
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_RESOURCE_POLICY_INVALID",
        )
        self.assertFalse(tampered_destination.exists())

        unknown = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        unknown_dir = self.root / unknown["archive"]
        (unknown_dir / "raw").mkdir()
        (unknown_dir / "raw" / "secret.pdb").write_text("private", encoding="utf-8")
        unknown_destination = self.root / "unknown.zip"
        response = export_screening_archive_zip(
            str(self.root),
            unknown["archive_id"],
            str(unknown_destination),
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_EXPORT_UNKNOWN_ENTRY",
        )
        self.assertFalse(unknown_destination.exists())

    def test_archive_zip_export_rejects_tampered_docking_output_identity(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        output_path = (
            archive_dir
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "out.pdbqt"
        )
        output_path.write_bytes(output_path.read_bytes() + b"\nTAMPERED")
        destination = self.root / "tampered-output.zip"

        response = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(destination),
        )

        self.assertFalse(response["ok"])
        self.assertIn(
            response["error"]["code"],
            {
                "SCREENING_ARCHIVE_EXPORT_OUTPUT_HASH_MISMATCH",
                "SCREENING_ARCHIVE_EXPORT_OUTPUT_SIZE_MISMATCH",
            },
        )
        self.assertFalse(destination.exists())

    def test_archive_zip_export_binds_output_identity_to_payload_snapshot(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        output_path = (
            archive_dir
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "out.pdbqt"
        )
        original = output_path.read_bytes()
        tampered = bytearray(original)
        tampered[-1] = (tampered[-1] + 1) % 256
        output_path.write_bytes(bytes(tampered))
        destination = self.root / "snapshot-bound-output.zip"
        from dockstart_core import screening as screening_module

        original_validator = (
            screening_module._archive_export_recorded_output_identity
        )

        def bypass_path_only_validation(
            record,
            path,
            *,
            archive_id,
            label,
            snapshot_record=None,
        ):
            if snapshot_record is None:
                return (
                    True,
                    str(record.get("output_sha256") or ""),
                    int(record.get("output_size_bytes") or 0),
                )
            return original_validator(
                record,
                path,
                archive_id=archive_id,
                label=label,
                snapshot_record=snapshot_record,
            )

        with patch(
            "dockstart_core.screening._archive_export_recorded_output_identity",
            side_effect=bypass_path_only_validation,
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_EXPORT_OUTPUT_HASH_MISMATCH",
        )
        self.assertFalse(destination.exists())

    def test_archive_zip_export_binds_resource_identity_to_payload_snapshot(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        receptor_path = archive_dir / "inputs" / "receptor.pdbqt"
        original = receptor_path.read_bytes()
        tampered = bytearray(original)
        tampered[-1] = (tampered[-1] + 1) % 256
        tampered_bytes = bytes(tampered)
        receptor_path.write_bytes(tampered_bytes)
        destination = self.root / "snapshot-bound-resource.zip"
        from dockstart_core import screening as screening_module

        original_validator = screening_module._validate_archived_resource_state

        def transiently_restore_resource(
            archive_directory,
            state,
            *,
            archive_id,
            error_code,
            verify_files,
            source_records=None,
        ):
            if source_records is not None:
                return original_validator(
                    archive_directory,
                    state,
                    archive_id=archive_id,
                    error_code=error_code,
                    verify_files=verify_files,
                    source_records=source_records,
                )
            receptor_path.write_bytes(original)
            try:
                return original_validator(
                    archive_directory,
                    state,
                    archive_id=archive_id,
                    error_code=error_code,
                    verify_files=verify_files,
                )
            finally:
                receptor_path.write_bytes(tampered_bytes)

        with patch(
            "dockstart_core.screening._validate_archived_resource_state",
            side_effect=transiently_restore_resource,
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_RESOURCE_POLICY_INVALID",
        )
        self.assertFalse(destination.exists())

    def test_archive_zip_export_binds_attempt_identity_to_payload_snapshot(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        config_path = (
            archive_dir
            / "attempts"
            / "ligand_0001"
            / "attempt_001"
            / "config.txt"
        )
        original = config_path.read_bytes()
        tampered = bytearray(original)
        tampered[-1] = (tampered[-1] + 1) % 256
        tampered_bytes = bytes(tampered)
        config_path.write_bytes(tampered_bytes)
        destination = self.root / "snapshot-bound-attempt.zip"
        from dockstart_core import screening as screening_module

        original_validator = screening_module._archive_attempt_integrity

        def transiently_restore_attempt(
            archive_directory,
            state,
            *,
            archive_id,
            source_records=None,
        ):
            if source_records is not None:
                return original_validator(
                    archive_directory,
                    state,
                    archive_id=archive_id,
                    source_records=source_records,
                )
            config_path.write_bytes(original)
            try:
                return original_validator(
                    archive_directory,
                    state,
                    archive_id=archive_id,
                )
            finally:
                config_path.write_bytes(tampered_bytes)

        with patch(
            "dockstart_core.screening._archive_attempt_integrity",
            side_effect=transiently_restore_attempt,
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_HASH_MISMATCH",
        )
        self.assertFalse(destination.exists())

    def test_archive_zip_export_binds_snapshot_to_validated_bundle(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        state_path = archive_dir / "screening.json"
        destination = self.root / "snapshot-race.zip"
        from dockstart_core import screening as screening_module

        original_scan = screening_module._scan_archive_export_tree
        scan_count = 0

        def scan_then_mutate(*args, **kwargs):
            nonlocal scan_count
            result = original_scan(*args, **kwargs)
            scan_count += 1
            if scan_count == 1:
                state_path.write_bytes(state_path.read_bytes() + b"\n")
            return result

        with patch(
            "dockstart_core.screening._scan_archive_export_tree",
            side_effect=scan_then_mutate,
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_STATE_HASH_MISMATCH",
        )
        self.assertFalse(destination.exists())

    def test_archive_zip_export_binds_manifest_state_hash_to_snapshot_bytes(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        state_path = archive_dir / "screening.json"
        manifest = json.loads(
            (archive_dir / "archive_manifest.json").read_text(encoding="utf-8")
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["top_n"] = max(1, int(state["top_n"]) - 1)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        destination = self.root / "manifest-state-snapshot.zip"
        from dockstart_core import screening as screening_module

        original_sha256 = screening_module._sha256
        expected_state_sha256 = manifest["state_sha256"]
        resolved_state = state_path.resolve()

        def transient_original_state_hash(path):
            if Path(path).resolve() == resolved_state:
                return expected_state_sha256
            return original_sha256(path)

        with patch(
            "dockstart_core.screening._sha256",
            side_effect=transient_original_state_hash,
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_STATE_HASH_MISMATCH",
        )
        self.assertFalse(destination.exists())

    def test_archive_zip_export_rejects_linked_payload_member(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        summary = archive_dir / "results" / "screening_summary.csv"
        target = archive_dir / "results" / "screening_top_n.csv"
        summary.unlink()
        try:
            summary.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"当前 Windows 环境不允许创建测试符号链接：{exc}")
        response = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(self.root / "linked.zip"),
        )
        self.assertFalse(response["ok"])
        self.assertIn(
            response["error"]["code"],
            {
                "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                "SCREENING_ARCHIVE_EXPORT_OUTPUT_HASH_MISMATCH",
            },
        )

    def test_archive_zip_export_destination_rules_and_explicit_overwrite(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        invalid_destinations = (
            self.root / "not-a-zip.txt",
            self.root / "missing-parent" / "archive.zip",
            archive_dir / "inside.zip",
            archive_dir.parent / "inside-archive-root.zip",
        )
        for destination in invalid_destinations:
            with self.subTest(destination=destination):
                response = export_screening_archive_zip(
                    str(self.root),
                    archived["archive_id"],
                    str(destination),
                )
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
                )

        destination = self.root / "existing.zip"
        destination.write_bytes(b"keep-me")
        refused = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(destination),
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"]["code"], "SCREENING_ARCHIVE_EXPORT_EXISTS")
        self.assertEqual(
            refused["error"]["details"],
            {
                "destination_file": str(destination.resolve()),
                "destination_sha256": hashlib.sha256(b"keep-me").hexdigest(),
                "destination_size_bytes": len(b"keep-me"),
            },
        )
        self.assertEqual(destination.read_bytes(), b"keep-me")

        replaced = export_screening_archive_zip(
            str(self.root),
            archived["archive_id"],
            str(destination),
            overwrite=True,
        )
        self.assertTrue(replaced["ok"], replaced)
        self.assertTrue(replaced["overwritten"])
        self.assertNotEqual(destination.read_bytes(), b"keep-me")

    def test_archive_zip_export_enforces_file_member_total_and_path_limits(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        cases = (
            ("MAX_SCREENING_EXPORT_FILES", 1),
            ("MAX_SCREENING_EXPORT_MEMBER_BYTES", 1),
            ("MAX_SCREENING_EXPORT_TOTAL_BYTES", 1),
            ("MAX_SCREENING_EXPORT_PATH_BYTES", 16),
        )
        for index, (constant, value) in enumerate(cases, start=1):
            destination = self.root / f"limited-{index}.zip"
            with self.subTest(constant=constant), patch(
                f"dockstart_core.screening.{constant}",
                value,
            ):
                response = export_screening_archive_zip(
                    str(self.root),
                    archived["archive_id"],
                    str(destination),
                )
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
                )
                self.assertFalse(destination.exists())

    def test_archive_zip_export_stops_directory_enumeration_at_hard_limit(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        destination = self.root / "enumeration-limit.zip"
        real_scandir = os.scandir

        class GuardedScandir:
            def __init__(self, path):
                self._inner = real_scandir(path)
                self._calls = 0

            def __enter__(self):
                self._inner.__enter__()
                return self

            def __exit__(self, *args):
                return self._inner.__exit__(*args)

            def __iter__(self):
                return self

            def __next__(self):
                self._calls += 1
                if self._calls > 3:
                    raise AssertionError("目录枚举超过硬限制后仍继续读取")
                return next(self._inner)

        with (
            patch("dockstart_core.screening.MAX_SCREENING_EXPORT_FILES", 2),
            patch(
                "dockstart_core.screening.os.scandir",
                side_effect=lambda path: GuardedScandir(path),
            ),
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
        )
        self.assertFalse(destination.exists())

    def test_archive_zip_export_detects_source_change_and_cleans_temporary_file(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / archived["archive"]
        destination = self.root / "changed-source.zip"
        from dockstart_core import screening as screening_module

        original_write = screening_module._write_screening_export_zip

        def write_then_mutate(*args, **kwargs):
            original_write(*args, **kwargs)
            report = archive_dir / "results" / "screening_report.md"
            report.write_bytes(report.read_bytes() + b"\nchanged")

        with patch(
            "dockstart_core.screening._write_screening_export_zip",
            side_effect=write_then_mutate,
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
        )
        self.assertFalse(destination.exists())
        self.assertFalse(any(self.root.glob(".changed-source.zip.*.tmp")))

    def test_archive_zip_export_failure_is_atomic_and_preserves_existing_target(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        destination = self.root / "atomic.zip"
        destination.write_bytes(b"old-archive")
        with patch(
            "dockstart_core.screening._verify_screening_export_zip",
            side_effect=RuntimeError("verification failed"),
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
                overwrite=True,
            )
        self.assertFalse(response["ok"])
        self.assertEqual(destination.read_bytes(), b"old-archive")
        self.assertFalse(any(self.root.glob(".atomic.zip.*.tmp")))

    def test_archive_zip_export_does_not_reopen_replaced_temp_path_for_writing(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        destination = self.root / "safe-temp-export.zip"
        victim = self.root / "victim.txt"
        victim.write_bytes(b"do-not-modify")
        safe_temporary = self.root / "isolated-temporary.bin"
        inner = safe_temporary.open("w+b")

        class MisnamedTemporaryHandle:
            name = str(victim)

            def __getattr__(self, name):
                return getattr(inner, name)

        with patch(
            "dockstart_core.screening.tempfile.NamedTemporaryFile",
            return_value=MisnamedTemporaryHandle(),
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )
        inner.close()
        safe_temporary.unlink(missing_ok=True)

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
        )
        self.assertEqual(victim.read_bytes(), b"do-not-modify")
        self.assertFalse(destination.exists())

    def test_archive_zip_export_rejects_same_inode_temp_content_rewrite(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        destination = self.root / "rewritten-temp.zip"
        from dockstart_core import screening as screening_module

        original_publish = screening_module._publish_screening_export_zip

        def rewrite_then_publish(temporary_file, publish_destination, **kwargs):
            Path(temporary_file).write_bytes(b"ATTACKER-CONTROLLED")
            return original_publish(
                temporary_file,
                publish_destination,
                **kwargs,
            )

        with patch(
            "dockstart_core.screening._publish_screening_export_zip",
            side_effect=rewrite_then_publish,
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
        )
        self.assertFalse(destination.exists())
        self.assertFalse(any(self.root.glob(".rewritten-temp.zip.*.tmp")))

    def test_archive_zip_export_rechecks_content_after_atomic_publish(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        destination = self.root / "post-publish-rewrite.zip"
        if os.name == "nt":
            original_publish_primitive = os.rename

            def publish_then_rewrite(source, target):
                original_publish_primitive(source, target)
                Path(target).write_bytes(b"ATTACKER-CONTROLLED")

            patch_target = "dockstart_core.screening.os.rename"
        else:
            original_publish_primitive = os.link

            def publish_then_rewrite(source, target):
                original_publish_primitive(source, target)
                Path(target).write_bytes(b"ATTACKER-CONTROLLED")

            patch_target = "dockstart_core.screening.os.link"

        with patch(patch_target, side_effect=publish_then_rewrite):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )

        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
        )
        self.assertFalse(destination.exists())
        self.assertFalse(any(self.root.glob(".post-publish-rewrite.zip.*.tmp")))

    @unittest.skipUnless(os.name == "nt", "Windows 原子 rename 回退测试")
    def test_archive_zip_export_no_overwrite_does_not_require_hardlinks_on_windows(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        destination = self.root / "windows-rename.zip"
        with patch(
            "dockstart_core.screening.os.link",
            side_effect=AssertionError("Windows 不应调用 os.link"),
        ):
            response = export_screening_archive_zip(
                str(self.root),
                archived["archive_id"],
                str(destination),
            )
        self.assertTrue(response["ok"], response)
        self.assertTrue(destination.is_file())

    def test_cli_archive_export_returns_one_json_document_and_supports_overwrite(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        destination = self.root / "cli-screening.zip"
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "archive-export",
                    "--project",
                    str(self.root),
                    "--archive-id",
                    archived["archive_id"],
                    "--output",
                    str(destination),
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"], payload)
        self.assertNotIn("manifest", payload)
        self.assertEqual(payload["zip_file"], str(destination.resolve()))
        self.assertTrue(destination.is_file())

        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "archive-export",
                    "--project",
                    str(self.root),
                    "--archive-id",
                    archived["archive_id"],
                    "--output",
                    str(destination),
                    "--overwrite",
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["overwritten"])

    def test_compare_archives_returns_score_and_rank_deltas_without_writing(self) -> None:
        baseline = self.create_completed_archive(
            runner=_successful_runner([], score_offset=0.0),
        )
        comparison = self.create_completed_archive(
            runner=_successful_runner([], score_offset=0.5),
        )
        archive_root = self.root / "screening" / "archive"
        before = {
            path.relative_to(archive_root).as_posix(): (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in archive_root.rglob("*")
            if path.is_file()
        }

        response = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )

        self.assertTrue(response["ok"], response)
        self.assertTrue(response["direct_score_comparison"])
        self.assertEqual(response["comparability"]["status"], "comparable")
        self.assertEqual(response["comparability"]["differences"], [])
        self.assertEqual(response["counts"]["matched"], 2)
        self.assertEqual(response["counts"]["delta_rows"], 2)
        self.assertEqual(response["counts"]["rows"], 2)
        for row in response["rows"]:
            self.assertEqual(row["match_status"], "matched")
            self.assertAlmostEqual(row["score_delta_kcal_mol"], 0.5)
            self.assertEqual(row["rank_delta"], 0)
            self.assertRegex(row["identity_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(len(row["baseline_items"]), 1)
            self.assertEqual(len(row["comparison_items"]), 1)
        fingerprint = response["baseline_archive"]["protocol_fingerprint"]
        self.assertEqual(fingerprint["receptor_sha256"], response["comparison_archive"]["protocol_fingerprint"]["receptor_sha256"])
        self.assertEqual(fingerprint["scoring"], "vina")
        self.assertEqual(set(fingerprint["box"]), set(BOX))
        self.assertEqual(fingerprint["vina_version"], "1.2.7")
        self.assertEqual(
            fingerprint["vina_binary_sha256"],
            hashlib.sha256(self.vina.read_bytes()).hexdigest(),
        )
        for key in (
            "exhaustiveness",
            "num_modes",
            "energy_range",
            "cpu",
            "seed",
            "max_evals",
            "min_rmsd",
            "spacing",
            "verbosity",
            "no_refine",
            "force_even_voxels",
            "fingerprint_sha256",
        ):
            self.assertIn(key, fingerprint)
        self.assertEqual(response["baseline_archive"]["input_integrity"], "verified")
        after = {
            path.relative_to(archive_root).as_posix(): (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in archive_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(self.project_json.read_bytes(), self.project_before)

    def test_compare_archives_with_protocol_difference_suppresses_all_deltas(self) -> None:
        baseline = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        changed_vina = {**VINA, "exhaustiveness": 16}
        comparison = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
            vina=changed_vina,
            runner=_successful_runner([], score_offset=1.0),
        )

        response = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )

        self.assertTrue(response["ok"], response)
        self.assertFalse(response["direct_score_comparison"])
        self.assertEqual(response["comparability"]["status"], "protocol_mismatch")
        self.assertEqual(
            [difference["field"] for difference in response["comparability"]["differences"]],
            ["exhaustiveness"],
        )
        self.assertEqual(response["rows"][0]["match_status"], "matched")
        self.assertNotIn("score_delta_kcal_mol", response["rows"][0])
        self.assertNotIn("rank_delta", response["rows"][0])
        self.assertEqual(response["counts"]["delta_rows"], 0)

    def test_compare_legacy_default_archive_matches_explicit_advanced_defaults(self) -> None:
        legacy = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
        )
        explicit_defaults = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
            vina={
                **VINA,
                "max_evals": 0,
                "min_rmsd": 1,
                "spacing": 0.375,
                "verbosity": 1,
                "no_refine": False,
                "force_even_voxels": False,
            },
        )
        legacy_dir = self.root / legacy["archive"]

        def remove_advanced_defaults(state):
            for key in (
                "max_evals",
                "min_rmsd",
                "spacing",
                "verbosity",
                "no_refine",
                "force_even_voxels",
            ):
                state["vina"].pop(key, None)

        self.rewrite_archived_state(legacy_dir, remove_advanced_defaults)
        legacy_detail = get_screening_archive(
            str(self.root),
            legacy["archive_id"],
        )
        self.assertTrue(legacy_detail["ok"], legacy_detail)
        self.assertEqual(
            legacy_detail["screening"]["compatibility"][
                "inferred_vina_fields"
            ],
            [
                "max_evals",
                "min_rmsd",
                "spacing",
                "verbosity",
                "no_refine",
                "force_even_voxels",
            ],
        )
        response = compare_screening_archives(
            str(self.root),
            [legacy["archive_id"], explicit_defaults["archive_id"]],
        )
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["direct_score_comparison"])
        self.assertEqual(response["comparability"]["differences"], [])
        fingerprint = response["baseline_archive"]["protocol_fingerprint"]
        self.assertEqual(fingerprint["max_evals"], 0)
        self.assertEqual(fingerprint["min_rmsd"], 1.0)
        self.assertEqual(fingerprint["spacing"], 0.375)
        self.assertEqual(fingerprint["verbosity"], 1)
        self.assertFalse(fingerprint["no_refine"])
        self.assertFalse(fingerprint["force_even_voxels"])

    def test_compare_archives_keeps_legacy_energy_range_zero_comparable(self) -> None:
        baseline = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
        )
        comparison = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
        )

        def make_legacy_zero(state):
            state["vina"]["energy_range"] = 0
            for key in (
                "max_evals",
                "min_rmsd",
                "spacing",
                "verbosity",
                "no_refine",
                "force_even_voxels",
            ):
                state["vina"].pop(key, None)

        self.rewrite_archived_state(
            self.root / baseline["archive"],
            make_legacy_zero,
        )
        self.rewrite_archived_state(
            self.root / comparison["archive"],
            make_legacy_zero,
        )
        response = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["direct_score_comparison"])
        self.assertEqual(
            response["baseline_archive"]["protocol_fingerprint"]["energy_range"],
            0.0,
        )

    def test_compare_archives_rejects_complete_new_protocol_with_energy_zero(self) -> None:
        candidate = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
        )
        reference = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
        )

        def forge_zero_without_legacy_origin(state):
            state["vina"]["energy_range"] = 0
            state.pop("compatibility", None)

        self.rewrite_archived_state(
            self.root / candidate["archive"],
            forge_zero_without_legacy_origin,
        )
        response = compare_screening_archives(
            str(self.root),
            [candidate["archive_id"], reference["archive_id"]],
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
        )

    def test_compare_archives_accepts_persisted_legacy_zero_marker(self) -> None:
        baseline = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
        )
        comparison = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
        )

        def mark_persisted_legacy_zero(state):
            state["vina"]["energy_range"] = 0
            state["compatibility"] = {
                "legacy_energy_range_zero": {
                    "applied": True,
                    "reason": "schema_v1_missing_all_advanced_vina_fields",
                    "detected_at": "2026-01-01T00:00:00+00:00",
                }
            }

        for archived in (baseline, comparison):
            self.rewrite_archived_state(
                self.root / archived["archive"],
                mark_persisted_legacy_zero,
            )
        response = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["direct_score_comparison"])

    def test_compare_archives_detects_advanced_vina_protocol_difference(self) -> None:
        baseline = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
        )
        comparison = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
            vina={**VINA, "max_evals": 250000},
        )
        response = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )
        self.assertTrue(response["ok"], response)
        self.assertFalse(response["direct_score_comparison"])
        self.assertEqual(
            [item["field"] for item in response["comparability"]["differences"]],
            ["max_evals"],
        )
        self.assertEqual(response["counts"]["delta_rows"], 0)

    def test_compare_archives_rejects_invalid_protocol_field_types(self) -> None:
        candidate = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        reference = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / candidate["archive"]
        state_path = archive_dir / "screening.json"
        manifest_path = archive_dir / "archive_manifest.json"
        original_state = state_path.read_bytes()
        original_manifest = manifest_path.read_bytes()
        cases = (
            ("scoring", "ad4"),
            ("exhaustiveness", True),
            ("num_modes", 9.0),
            ("cpu", "2"),
            ("energy_range", float("inf")),
            ("seed", False),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                state_path.write_bytes(original_state)
                manifest_path.write_bytes(original_manifest)

                def mutate(state, field=field, value=value):
                    state["vina"][field] = value

                self.rewrite_archived_state(archive_dir, mutate)
                response = compare_screening_archives(
                    str(self.root),
                    [candidate["archive_id"], reference["archive_id"]],
                )
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                )
        state_path.write_bytes(original_state)
        manifest_path.write_bytes(original_manifest)

    def test_compare_archives_recreates_protocol_resource_bounds(self) -> None:
        candidate = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        reference = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"]
        )
        archive_dir = self.root / candidate["archive"]
        state_path = archive_dir / "screening.json"
        manifest_path = archive_dir / "archive_manifest.json"
        original_state = state_path.read_bytes()
        original_manifest = manifest_path.read_bytes()
        cases = (
            "box_archived_limit",
            "box_default_limit",
            "exhaustiveness",
            "num_modes",
            "cpu",
            "energy_range",
            "seed",
        )
        for case in cases:
            with self.subTest(case=case):
                state_path.write_bytes(original_state)
                manifest_path.write_bytes(original_manifest)

                def mutate(state, case=case):
                    if case == "box_archived_limit":
                        state["resource_limits"]["max_box_edge_angstrom"] = 19
                    elif case == "box_default_limit":
                        state.pop("resource_limits", None)
                        state["box"]["size_x"] = 127
                    elif case == "exhaustiveness":
                        state["vina"]["exhaustiveness"] = (
                            state["resource_limits"]["max_exhaustiveness"] + 1
                        )
                    elif case == "num_modes":
                        state["vina"]["num_modes"] = (
                            state["resource_limits"]["max_num_modes"] + 1
                        )
                    elif case == "cpu":
                        state["vina"]["cpu"] = (
                            state["resource_limits"]["max_cpu"] + 1
                        )
                    elif case == "energy_range":
                        state["vina"]["energy_range"] = 20.1
                    elif case == "seed":
                        state["vina"]["seed"] = 2_147_483_648

                self.rewrite_archived_state(archive_dir, mutate)
                response = compare_screening_archives(
                    str(self.root),
                    [candidate["archive_id"], reference["archive_id"]],
                )
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                )
        state_path.write_bytes(original_state)
        manifest_path.write_bytes(original_manifest)

    def test_compare_archives_matches_same_content_with_different_names(self) -> None:
        baseline = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        renamed = self.root / "prepared" / "renamed-candidate.pdbqt"
        renamed.write_bytes((self.root / "prepared" / "alpha.pdbqt").read_bytes())
        comparison = self.create_completed_archive(
            ligand_files=["prepared/renamed-candidate.pdbqt"],
        )

        response = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["counts"]["matched"], 1)
        row = response["rows"][0]
        self.assertEqual(row["match_status"], "matched")
        self.assertEqual(row["baseline_items"][0]["display_label"], "alpha.pdbqt")
        self.assertEqual(
            row["comparison_items"][0]["display_label"],
            "renamed-candidate.pdbqt",
        )
        self.assertEqual(
            row["baseline_items"][0]["identity_basis"],
            "pdbqt_sha256",
        )

    def test_compare_archives_does_not_match_same_pdbqt_with_different_topology(
        self,
    ) -> None:
        def create_modern_archive(
            filename: str,
            raw_record: bytes,
            canonical_topology_sha256: str,
        ) -> dict:
            source = self.root / filename
            source.write_bytes(raw_record)
            with (
                patch(
                    "dockstart_core.screening.get_preparation_tool_status",
                    return_value=_screening_preparation_tools(),
                ),
                patch(
                    "dockstart_core.screening._prepare_raw_screening_library",
                    side_effect=_fake_library_worker(
                        [
                            {
                                "index": 1,
                                "status": "ready",
                                "atom": "C",
                                "canonical_sha256": (
                                    canonical_topology_sha256
                                ),
                            }
                        ]
                    ),
                ),
            ):
                staged = stage_screening_inputs(
                    str(self.root),
                    [str(source)],
                )
            self.assertTrue(staged["ok"], staged)
            candidate = staged["import_preview"]["candidates"][0]
            created = self.create(
                ligand_files=[],
                ligand_candidate_ids=[candidate["candidate_id"]],
                expected_staging_revision_sha256=staged["import_preview"][
                    "revision_sha256"
                ],
            )
            self.assertTrue(created["ok"], created)
            with patch(
                "dockstart_core.screening._generate_screening_result_sdf",
                return_value={},
            ):
                finished = run_screening(
                    str(self.root),
                    runner=_successful_runner([]),
                )
            self.assertTrue(finished["ok"], finished)
            archived = archive_screening(str(self.root))
            self.assertTrue(archived["ok"], archived)
            return archived

        baseline = create_modern_archive(
            "topology-a.sdf",
            b"topology a\nmock\n$$$$\n",
            "1" * 64,
        )
        comparison = create_modern_archive(
            "topology-b.sdf",
            b"topology b\nmock\n$$$$\n",
            "2" * 64,
        )

        response = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["counts"]["matched"], 0)
        self.assertEqual(response["counts"]["baseline_only"], 1)
        self.assertEqual(response["counts"]["comparison_only"], 1)
        self.assertEqual(response["counts"]["rows"], 2)
        self.assertEqual(
            {row["match_status"] for row in response["rows"]},
            {"baseline_only", "comparison_only"},
        )
        self.assertEqual(
            len({row["identity_sha256"] for row in response["rows"]}),
            2,
        )
        summaries = [
            item
            for row in response["rows"]
            for side in ("baseline_items", "comparison_items")
            for item in row[side]
        ]
        self.assertEqual(len(summaries), 2)
        self.assertEqual(
            len({item["pdbqt_sha256"] for item in summaries}),
            1,
        )
        self.assertEqual(
            {
                item["canonical_topology_sha256"]
                for item in summaries
            },
            {"1" * 64, "2" * 64},
        )
        self.assertTrue(
            all(
                item["identity_basis"]
                == "pdbqt_sha256+canonical_topology_sha256"
                for item in summaries
            )
        )
        self.assertTrue(
            all(item["source_candidate_id"] for item in summaries)
        )

    def test_compare_archives_does_not_match_same_name_with_different_content(self) -> None:
        baseline = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        (self.root / "prepared" / "alpha.pdbqt").write_text(
            _pdbqt("S"),
            encoding="utf-8",
        )
        comparison = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])

        response = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["counts"]["matched"], 0)
        self.assertEqual(response["counts"]["baseline_only"], 1)
        self.assertEqual(response["counts"]["comparison_only"], 1)
        self.assertEqual(
            {row["match_status"] for row in response["rows"]},
            {"baseline_only", "comparison_only"},
        )
        self.assertNotEqual(
            response["rows"][0]["identity_sha256"],
            response["rows"][1]["identity_sha256"],
        )

    def test_compare_archives_marks_duplicate_content_ambiguous_without_guessing(self) -> None:
        duplicate = self.root / "prepared" / "alpha-copy.pdbqt"
        duplicate.write_bytes((self.root / "prepared" / "alpha.pdbqt").read_bytes())
        baseline = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt", "prepared/alpha-copy.pdbqt"],
        )
        comparison = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])

        response = compare_screening_archives(
            str(self.root),
            [baseline["archive_id"], comparison["archive_id"]],
        )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["counts"]["ambiguous"], 1)
        self.assertEqual(response["counts"]["matched"], 0)
        row = response["rows"][0]
        self.assertEqual(row["match_status"], "ambiguous")
        self.assertEqual(len(row["baseline_items"]), 2)
        self.assertEqual(len(row["comparison_items"]), 1)
        self.assertNotIn("score_delta_kcal_mol", row)
        self.assertNotIn("rank_delta", row)

    def test_compare_archives_rejects_damaged_state_and_tampered_inputs(self) -> None:
        damaged = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        tampered = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        receptor_tampered = self.create_completed_archive(
            ligand_files=["prepared/alpha.pdbqt"],
        )
        reference = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])

        damaged_state = self.root / damaged["archive"] / "screening.json"
        damaged_state.write_text(
            damaged_state.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        response = compare_screening_archives(
            str(self.root),
            [damaged["archive_id"], reference["archive_id"]],
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_STATE_HASH_MISMATCH",
        )

        ligand_path = (
            self.root
            / tampered["archive"]
            / "inputs"
            / "ligands"
            / "ligand_0001.pdbqt"
        )
        content = bytearray(ligand_path.read_bytes())
        content[0] ^= 1
        ligand_path.write_bytes(content)
        response = compare_screening_archives(
            str(self.root),
            [tampered["archive_id"], reference["archive_id"]],
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_COMPARE_INPUT_HASH_MISMATCH",
        )

        receptor_path = (
            self.root
            / receptor_tampered["archive"]
            / "inputs"
            / "receptor.pdbqt"
        )
        receptor_path.write_bytes(receptor_path.read_bytes() + b"\n")
        response = compare_screening_archives(
            str(self.root),
            [receptor_tampered["archive_id"], reference["archive_id"]],
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_COMPARE_INPUT_SIZE_MISMATCH",
        )

    def test_compare_archives_rejects_legacy_missing_input_sha(self) -> None:
        legacy = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        reference = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        archive_dir = self.root / legacy["archive"]

        def remove_input_sha(state):
            state["items"][0].pop("sha256")

        self.rewrite_archived_state(archive_dir, remove_input_sha)
        response = compare_screening_archives(
            str(self.root),
            [legacy["archive_id"], reference["archive_id"]],
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "SCREENING_ARCHIVE_COMPARE_HASH_MISSING",
        )

    def test_compare_archives_requires_exactly_two_distinct_archives(self) -> None:
        archived = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        cases = (
            [],
            [archived["archive_id"]],
            [archived["archive_id"], archived["archive_id"]],
            [archived["archive_id"], "screening_999_20260101000000", "third"],
        )
        for archive_ids in cases:
            with self.subTest(archive_ids=archive_ids):
                response = compare_screening_archives(str(self.root), archive_ids)
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "SCREENING_ARCHIVE_COMPARE_SELECTION_INVALID",
                )

    def test_cli_archive_compare_accepts_repeated_archive_id_flags(self) -> None:
        baseline = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        comparison = self.create_completed_archive(ligand_files=["prepared/alpha.pdbqt"])
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "archive-compare",
                    "--project",
                    str(self.root),
                    "--archive-id",
                    baseline["archive_id"],
                    "--archive-id",
                    comparison["archive_id"],
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(
            payload["baseline_archive"]["archive_id"],
            baseline["archive_id"],
        )
        self.assertEqual(
            payload["comparison_archive"]["archive_id"],
            comparison["archive_id"],
        )

    def test_archive_refuses_nonterminal_job_and_create_never_overwrites(self) -> None:
        self.assertTrue(self.create()["ok"])
        duplicate = self.create()
        self.assertFalse(duplicate["ok"])
        self.assertEqual(duplicate["error"]["code"], "SCREENING_ALREADY_EXISTS")
        refused = archive_screening(str(self.root))
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"]["code"], "SCREENING_NOT_TERMINAL")
        self.assertTrue((self.root / "screening" / "screening.json").is_file())

    def test_ready_job_can_be_canceled_immediately_then_archived(self) -> None:
        self.assertTrue(self.create()["ok"])
        canceled = request_screening_cancel(str(self.root))
        self.assertTrue(canceled["ok"], canceled)
        self.assertEqual(canceled["screening"]["status"], "canceled")
        archived = archive_screening(str(self.root))
        self.assertTrue(archived["ok"], archived)

    def test_resume_refuses_while_recorded_pid_is_alive(self) -> None:
        self.assertTrue(self.create()["ok"])
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "running"
        state["items"][0]["status"] = "running"
        state["items"][0]["attempts"] = [{"status": "running", "pid": 4242}]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with patch("dockstart_core.screening.vina_adapter.is_process_running", return_value=True):
            response = resume_screening(str(self.root))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "SCREENING_PROCESS_ACTIVE")
        unchanged = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(unchanged["items"][0]["status"], "running")

    def test_runner_started_callback_persists_pid_before_completion(self) -> None:
        self.assertTrue(self.create(ligand_files=["prepared/alpha.pdbqt"])["ok"])

        def runner(**kwargs):
            kwargs["on_started"](5151)
            current = json.loads(
                (self.root / "screening" / "screening.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(current["items"][0]["attempts"][0]["pid"], 5151)
            kwargs["output_path"].write_text(_pdbqt(), encoding="utf-8")
            kwargs["log_path"].write_text("   1      -7.00      0.000      0.000\n", encoding="utf-8")
            return {"exit_code": 0}

        response = run_screening(str(self.root), runner=runner)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["screening"]["items"][0]["attempts"][0]["pid"], 5151)

    def test_resource_limits_are_rejected_before_state_creation(self) -> None:
        response = self.create(
            resource_limits={"max_ligands": 1},
        )
        self.assertFalse(response["ok"])
        self.assertIn("资源上限", response["error"]["raw_error"])
        self.assertFalse((self.root / "screening" / "screening.json").exists())

    def test_project_escape_is_rejected(self) -> None:
        outside = self.root.parent / "outside-screening-ligand.pdbqt"
        outside.write_text(_pdbqt(), encoding="utf-8")
        try:
            response = self.create(ligand_files=[str(outside)])
            self.assertFalse(response["ok"])
            self.assertIn("项目目录内", response["error"]["raw_error"])
        finally:
            outside.unlink(missing_ok=True)

    def test_max_retries_finishes_with_failure_and_keeps_error(self) -> None:
        self.assertTrue(self.create(max_retries=0, ligand_files=["prepared/alpha.pdbqt"])["ok"])

        def failed_runner(**_kwargs):
            return {"exit_code": 9, "error": "mock failure"}

        response = run_screening(str(self.root), runner=failed_runner)
        self.assertEqual(response["screening"]["status"], "completed_with_failures")
        self.assertEqual(response["screening"]["items"][0]["attempt_count"], 1)
        self.assertEqual(response["screening"]["items"][0]["last_error"], "mock failure")

    def test_cli_status_prints_one_json_document(self) -> None:
        self.assertTrue(self.create()["ok"])
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["status", "--project", str(self.root)])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["screening"]["status"], "ready")

    def test_cli_stage_and_archive_commands_return_json(self) -> None:
        with tempfile.TemporaryDirectory() as external_dir:
            source = Path(external_dir) / "cli-input.pdbqt"
            source.write_text(_pdbqt(), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["stage", "--project", str(self.root), "--file", str(source)],
                )
            self.assertEqual(exit_code, 0)
            staged = json.loads(output.getvalue())
            self.assertTrue(staged["staged"][0]["file"].startswith("screening/staging/"))

        self.assertTrue(self.create()["ok"])
        self.assertTrue(run_screening(str(self.root), runner=_successful_runner([]))["ok"])
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["archive", "--project", str(self.root)])
        self.assertEqual(exit_code, 0)
        archived = json.loads(output.getvalue())
        self.assertTrue(archived["ok"])
        self.assertTrue((self.root / archived["archive"] / "screening.json").is_file())

    def test_interrupted_batch_requires_explicit_resume(self) -> None:
        self.assertTrue(self.create()["ok"])
        calls: list[tuple[str, int]] = []
        response = run_screening(str(self.root), runner=_successful_runner(calls), max_items=1)
        self.assertEqual(response["screening"]["status"], "interrupted")
        refused = run_screening(str(self.root), runner=_successful_runner(calls))
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"]["code"], "SCREENING_NOT_READY")
        self.assertTrue(resume_screening(str(self.root))["ok"])


if __name__ == "__main__":
    unittest.main()
