from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = ROOT / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import ad4_v0141_common as common  # noqa: E402
import verify_ad4_flexible_1fpu as flexible_verifier  # noqa: E402
import verify_ad4_multiple_ligands_5x72 as multiple_verifier  # noqa: E402
import verify_ad4_serial_screening as screening_verifier  # noqa: E402
import verify_ad4_v0141_evidence_bundle as bundle  # noqa: E402


def _manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _record(label: str, *, size: int | None = None, sha256: str | None = None) -> dict:
    normalized = label.replace("\\", "/")
    return {
        "path": f"C:/frozen/{normalized}",
        "size_bytes": int(size if size is not None else len(label.encode("utf-8")) + 100),
        "sha256": str(sha256 or _sha(label)),
    }


def _identity(record: dict, label: str) -> dict:
    return _record(
        label,
        size=int(record["size_bytes"]),
        sha256=str(record["sha256"]),
    )


def _copy_record(record: dict, root: str) -> dict:
    basename = str(record["path"]).replace("\\", "/").rsplit("/", 1)[-1]
    return _record(
        f"{root}/{basename}",
        size=int(record["size_bytes"]),
        sha256=str(record["sha256"]),
    )


def _passed_steps(names: tuple[str, ...]) -> dict[str, str]:
    return {name: "passed" for name in names}


def _tools(*, include_meeko: bool = False) -> dict:
    vina = {
        **_record(
            "tools/vina.exe",
            size=int(common.VINA_1_2_7_CONTRACT["size_bytes"]),
            sha256=str(common.VINA_1_2_7_CONTRACT["sha256"]),
        ),
        "detected_status": "ok",
        "detected_version": "1.2.7",
        "detected_path": "C:/frozen/tools/vina.exe",
    }
    autogrid = {
        **_record("tools/autogrid4.exe", size=1_927_292),
        "detected_status": "ok",
        "detected_version": "4.2.6",
        "detected_path": "C:/frozen/tools/autogrid4.exe",
    }
    result = {"vina": vina, "autogrid4": autogrid}
    if include_meeko:
        result["meeko"] = {
            **_record("tools/python.exe", size=91_648),
            "detected_status": "ok",
            "detected_version": "0.7.1",
            "detected_path": "C:/frozen/tools/python.exe",
        }
    return result


def _maps(
    tools: dict,
    *,
    atom_types: list[str],
    receptor: dict,
    ligand: dict,
) -> dict:
    required_files = [
        "receptor.maps.fld",
        *(f"receptor.{atom_type}.map" for atom_type in atom_types),
        "receptor.e.map",
        "receptor.d.map",
    ]
    files = [_record(f"maps/{name}") for name in [*required_files, "receptor.maps.xyz"]]
    autogrid = tools["autogrid4"]
    return {
        "manifest": _record("maps/manifest.json"),
        "gpf": _record("maps/receptor.gpf"),
        "glg": _record("maps/autogrid.glg"),
        "autogrid": {
            "path": autogrid["path"],
            "version": autogrid["detected_version"],
            "sha256": autogrid["sha256"],
            "command": [
                autogrid["path"],
                "-p",
                "receptor.gpf",
                "-l",
                "autogrid.glg",
            ],
            "exit_code": 0,
            "identity": dict(autogrid),
            "log_summary": {
                "successful_completion": True,
                "has_error": False,
                "error_lines": [],
            },
        },
        "ligand_atom_types": list(atom_types),
        "required_files": required_files,
        "files": files,
        "receptor": {
            **_record(
                "maps/inputs/receptor.pdbqt",
                size=receptor["size_bytes"],
                sha256=receptor["sha256"],
            ),
            "source_sha256": receptor["sha256"],
            "verified_identity": _record(
                "maps/verified/receptor.pdbqt",
                size=receptor["size_bytes"],
                sha256=receptor["sha256"],
            ),
        },
        "ligand": {
            **_record(
                "maps/inputs/ligand.pdbqt",
                size=ligand["size_bytes"],
                sha256=ligand["sha256"],
            ),
            "source_sha256": ligand["sha256"],
            "verified_identity": _record(
                "maps/verified/ligand.pdbqt",
                size=ligand["size_bytes"],
                sha256=ligand["sha256"],
            ),
        },
        "flexible_receptor": {},
    }


def _flexible_payload(*, schema_version: int = 2) -> dict:
    manifest = _manifest(bundle.FLEXIBLE_MANIFEST)
    receptor = _identity(manifest["source_files"]["receptor_h_pdb"], "inputs/1fpu.pdb")
    ligand = _identity(manifest["source_files"]["ligand_pdbqt"], "inputs/ligand.pdbqt")
    frozen_hashes = manifest["preparation_contract"]["output_sha256"]
    outputs = {
        "rigid_pdbqt": _record("prepared/rigid.pdbqt", size=196_182, sha256=frozen_hashes["rigid_pdbqt"]),
        "flex_pdbqt": _record("prepared/flex.pdbqt", size=572, sha256=frozen_hashes["flex_pdbqt"]),
        "receptor_json": _record("prepared/receptor.json", size=1_024, sha256=frozen_hashes["receptor_json"]),
    }
    tools = _tools(include_meeko=True)
    maps = _maps(
        tools,
        atom_types=["A", "C", "HD", "N", "NA", "OA"],
        receptor=outputs["rigid_pdbqt"],
        ligand=ligand,
    )
    maps["flexible_receptor"] = {
        "mode": "flexible",
        "preparation_id": "flex_001",
        "rigid_sha256": frozen_hashes["rigid_pdbqt"],
        "flex_sha256": frozen_hashes["flex_pdbqt"],
        "selected_residues": [{"selector": "A:315"}],
        "verified_identity": _record(
            "maps/verified/flex.pdbqt",
            size=outputs["flex_pdbqt"]["size_bytes"],
            sha256=outputs["flex_pdbqt"]["sha256"],
        ),
    }
    return {
        "schema_version": schema_version,
        "verifier_id": bundle.FLEXIBLE_ID,
        "ok": True,
        "steps": _passed_steps(bundle.FLEXIBLE_STEPS),
        "fixture": {
            "bad_residue_contract": _identity(
                manifest["preparation_contract"]["bad_residue_contract"],
                "fixtures/expected_bad_residues.json",
            )
        },
        "inputs": {"receptor": receptor, "ligand": ligand},
        "tools": tools,
        "preparation": {
            "preparation_id": "flex_001",
            "selected_residues": ["A:315"],
            "output_sha256": dict(frozen_hashes),
            "outputs": outputs,
        },
        "maps": maps,
        "run": {
            "command": [
                tools["vina"]["path"],
                "--config",
                "runs/run_001/config_snapshot.txt",
                "--maps",
                "runs/run_001/inputs/maps/receptor",
                "--scoring",
                "ad4",
                "--out",
                "runs/run_001/out.pdbqt",
                "--flex",
                "runs/run_001/inputs/flex.pdbqt",
            ],
            "status": "finished",
            "best_affinity_kcal_mol": -14.2,
            "first_pose_no_fit_heavy_atom_rmsd_angstrom": 1.086823,
            "output": _record("runs/run_001/out.pdbqt"),
        },
        "report": _record("runs/run_001/docking_report.md"),
    }


def _multiple_payload(*, schema_version: int = 2) -> dict:
    manifest = _manifest(bundle.MULTIPLE_MANIFEST)
    sources = manifest["source_files"]
    receptor = _identity(sources["receptor_pdbqt"], "inputs/receptor.pdbqt")
    members = [
        _identity(sources["ligand_p59_pdbqt"], "inputs/ligand_001.pdbqt"),
        _identity(sources["ligand_p69_pdbqt"], "inputs/ligand_002.pdbqt"),
    ]
    tools = _tools()
    maps = _maps(
        tools,
        atom_types=["A", "C", "F", "HD", "N", "OA"],
        receptor=receptor,
        ligand=members[0],
    )
    return {
        "schema_version": schema_version,
        "verifier_id": bundle.MULTIPLE_ID,
        "ok": True,
        "steps": _passed_steps(bundle.MULTIPLE_STEPS),
        "inputs": {"receptor": receptor, "ordered_members": members},
        "tools": tools,
        "maps": maps,
        "run": {
            "command": [
                tools["vina"]["path"],
                "--config",
                "runs/run_001/config_snapshot.txt",
                "--ligand",
                "runs/run_001/inputs/ligand_001.pdbqt",
                "runs/run_001/inputs/ligand_002.pdbqt",
                "--maps",
                "runs/run_001/inputs/ad4_maps/receptor",
                "--scoring",
                "ad4",
                "--out",
                "runs/run_001/out.pdbqt",
            ],
            "member_order": list(manifest["protocol"]["member_order"]),
            "member_sha256": [members[0]["sha256"], members[1]["sha256"]],
            "frozen_map_files": [
                _copy_record(record, "run/maps") for record in maps["files"]
            ],
            "pose_count": 9,
            "best_joint_affinity_kcal_mol": -17.65,
            "score_scope": "joint_two_ligand_pose",
            "per_member_scores_available": False,
            "output": _record("runs/run_001/out.pdbqt"),
            "joint_poses": _record("runs/run_001/joint_poses.json"),
        },
        "report": _record("runs/run_001/multi_ligand_report.md"),
    }


def _screening_payload(*, schema_version: int = 2) -> dict:
    manifest = _manifest(bundle.SCREENING_MANIFEST)
    sources = manifest["source_files"]
    receptor = _identity(sources["receptor_pdbqt"], "inputs/receptor.pdbqt")
    source_ligands = [
        _identity(sources["ligand_p59_pdbqt"], "inputs/p59.pdbqt"),
        _identity(sources["ligand_p69_pdbqt"], "inputs/p69.pdbqt"),
    ]
    tools = _tools()
    generated_maps = _maps(
        tools,
        atom_types=["A", "C", "F", "HD", "N", "OA"],
        receptor=receptor,
        ligand=source_ligands[0],
    )
    source_order = list(manifest["workload"]["source_order"])
    workload = []
    for order, source in enumerate(source_order, start=1):
        marker = f"REMARK DOCKSTART V0141 SERIAL AUDIT ITEM {order:02d} SOURCE {source}"
        workload.append(
            {
                **_record(f"workload/audit_{order:02d}.pdbqt", size=2_400 + order),
                "order": order,
                "source": source,
                "marker": marker,
                "relative_path": f"prepared/audit_{order:02d}.pdbqt",
            }
        )
    failed_order = int(manifest["fault_injection"]["intentionally_fail_item_order"])
    map_prefix = "../../../inputs/ad4_maps/receptor"
    config = _record("attempts/config.txt", size=152)
    attempts = []
    successful = []
    for order, workload_record in enumerate(workload, start=1):
        failed = order == failed_order
        affinity = None if failed else (-9.229 if order % 2 else -8.373)
        item_id = f"ligand_{order:04d}"
        attempt = {
            "order": order,
            "item_id": item_id,
            "status": "failed" if failed else "succeeded",
            "exit_code": 97 if failed else 0,
            "command": [
                tools["vina"]["path"],
                "--config",
                "config.txt",
                "--maps",
                map_prefix,
                "--scoring",
                "ad4",
                "--out",
                "out.pdbqt",
            ],
            "receptor": _record(
                f"attempts/{item_id}/receptor.pdbqt",
                size=receptor["size_bytes"],
                sha256=receptor["sha256"],
            ),
            "ligand": _record(
                f"attempts/{item_id}/ligand.pdbqt",
                size=workload_record["size_bytes"],
                sha256=workload_record["sha256"],
            ),
            "config": _record(
                f"attempts/{item_id}/config.txt",
                size=config["size_bytes"],
                sha256=config["sha256"],
            ),
            "output": None if failed else _record(f"attempts/{item_id}/out.pdbqt"),
            "best_affinity_kcal_mol": affinity,
        }
        attempts.append(attempt)
        if not failed:
            successful.append((float(affinity), order, item_id))
    ranked = sorted(successful, key=lambda value: (value[0], value[1]))
    archive_id = "screening_001_test"
    return {
        "schema_version": schema_version,
        "verifier_id": bundle.SCREENING_ID,
        "ok": True,
        "steps": _passed_steps(bundle.SCREENING_STEPS),
        "inputs": {
            "receptor": receptor,
            "source_ligands": source_ligands,
            "derived_workload": workload,
        },
        "tools": tools,
        "maps": {
            "generated": generated_maps,
            "screening_frozen_files": [
                _copy_record(record, "screening/maps")
                for record in generated_maps["files"]
            ],
            "tamper_resume_rejection": {
                "ok": False,
                "error": {
                    "code": manifest["acceptance"]["tampered_resume_error_code"]
                },
            },
        },
        "cancellation": {
            "item_order": manifest["fault_injection"][
                "request_cancel_while_item_order_is_running"
            ],
            "process_alive_before_request": True,
            "response": {"ok": True},
        },
        "screening": {
            "status": manifest["acceptance"]["final_status"],
            "succeeded_count": manifest["acceptance"]["succeeded_count"],
            "failed_count": manifest["acceptance"]["failed_count"],
            "attempts": attempts,
            "frozen_attempt_map_prefix": map_prefix,
            "ranking": {
                "summary": _record("screening/results/summary.csv"),
                "ranked_top_n": _record("screening/results/top_n.csv"),
                "ranked_item_ids": [value[2] for value in ranked],
                "ranked_affinities_kcal_mol": [value[0] for value in ranked],
            },
        },
        "report": _record("screening/results/report.md"),
        "archive": {
            "archive_id": archive_id,
            "relative_path": f"screening/archive/{archive_id}",
            "zip": _record("screening/archive.zip", size=8_377_370),
            "zip_entry_count": 126,
            "payload_tree_sha256": _sha("archive-tree"),
        },
    }


def _source_bound(payload: dict) -> dict:
    contract = bundle.GATE_CONTRACTS[payload["verifier_id"]]
    session = _synthetic_imports_verified_session(payload["verifier_id"])
    return common.bind_source_provenance(
        payload,
        repo_root=ROOT,
        verifier_path=contract["verifier_path"],
        fixture_manifest_paths=[contract["manifest_path"]],
        source_session=session,
    )


def _synthetic_imports_verified_session(verifier_id: str) -> dict:
    """Build a same-process fixture session; standalone gate order is tested separately."""

    contract = bundle.GATE_CONTRACTS[verifier_id]
    snapshot = common.capture_source_bound_session(
        repo_root=ROOT,
        verifier_id=verifier_id,
        verifier_path=contract["verifier_path"],
        fixture_manifest_paths=[contract["manifest_path"]],
        validate_import_origins=True,
    )
    return {
        **snapshot,
        "phase": "imports_verified",
        "imports_verified_at_utc": snapshot["captured_at_utc"],
        "imports_verified_source_fingerprint": dict(snapshot["source_fingerprint"]),
    }


def _legacy_payloads() -> list[dict]:
    return [
        _flexible_payload(schema_version=1),
        _multiple_payload(schema_version=1),
        _screening_payload(schema_version=1),
    ]


class Ad4V0141EvidenceBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        flexible_verifier._load_backend_modules()
        multiple_verifier._load_backend_modules()
        screening_verifier._load_backend_modules()
        cls.source_bound_payloads = [
            _source_bound(_flexible_payload()),
            _source_bound(_multiple_payload()),
            _source_bound(_screening_payload()),
        ]

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="DockStart_bundle_test_")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_payloads(self, payloads: list[dict]) -> list[Path]:
        paths = []
        for index, payload in enumerate(payloads, start=1):
            path = self.root / f"evidence_{index}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            paths.append(path)
        return paths

    def _verify(self, payloads: list[dict], *, require_source_bound: bool = False) -> dict:
        return bundle.verify_bundle(
            self._write_payloads(payloads),
            require_source_bound=require_source_bound,
        )

    def _payloads(self) -> list[dict]:
        return copy.deepcopy(self.source_bound_payloads)

    def assertErrorCode(self, result: dict, code: str) -> None:  # noqa: N802
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], code)
        self.assertEqual(result["provenance"]["binding_status"], "source_bound")

    def test_current_source_bound_bundle_passes(self) -> None:
        result = self._verify(self._payloads(), require_source_bound=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["binding_status"], "source_bound")
        self.assertEqual(result["gate_count"], 3)
        self.assertTrue(all(gate["binding_status"] == "source_bound" for gate in result["gates"]))
        self.assertGreater(result["provenance"]["backend_validation_tree"]["file_count"], 0)
        for payload in self.source_bound_payloads:
            guard = payload["provenance"]["execution_source_guard"]
            timestamps = [
                datetime.fromisoformat(guard[key].replace("Z", "+00:00"))
                for key in (
                    "pre_import_captured_at_utc",
                    "imports_verified_at_utc",
                    "end_captured_at_utc",
                )
            ]
            self.assertEqual(timestamps, sorted(timestamps))
            self.assertEqual(
                payload["provenance"]["generated_at_utc"],
                guard["end_captured_at_utc"],
            )

    def test_gate_modules_capture_source_before_backend_import_in_fresh_process(self) -> None:
        for module_name in (
            "verify_ad4_flexible_1fpu",
            "verify_ad4_multiple_ligands_5x72",
            "verify_ad4_serial_screening",
        ):
            with self.subTest(module=module_name):
                code = (
                    "import sys; "
                    f"sys.path.insert(0, {str(SCRIPT_ROOT)!r}); "
                    f"import {module_name} as gate; "
                    "assert 'adapters' not in sys.modules; "
                    "assert 'dockstart_core' not in sys.modules; "
                    "session=gate._capture_source_session(); "
                    "assert session['phase']=='imports_verified'; "
                    "assert session['import_origins']['status']=='verified'"
                )
                completed = subprocess.run(
                    [sys.executable, "-c", code],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=60,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_gate_preimport_snapshot_rejects_reused_process(self) -> None:
        contract = bundle.GATE_CONTRACTS[bundle.FLEXIBLE_ID]
        with self.assertRaises(common.AcceptanceError) as raised:
            common.capture_source_bound_session(
                repo_root=ROOT,
                verifier_id=bundle.FLEXIBLE_ID,
                verifier_path=contract["verifier_path"],
                fixture_manifest_paths=[contract["manifest_path"]],
                validate_import_origins=False,
            )
        self.assertEqual(
            raised.exception.code,
            "AD4_ACCEPTANCE_PREIMPORT_MODULE_ALREADY_LOADED",
        )

    def test_preimport_capture_failure_never_loads_backend_or_enters_workflow(self) -> None:
        missing = self.root / "missing.pdbqt"
        invocations = [
            (
                flexible_verifier,
                ["--ligand-pdbqt", str(missing), "--autogrid4", str(missing), "--autogrid4-sha256", "0" * 64],
            ),
            (
                multiple_verifier,
                ["--receptor-pdbqt", str(missing), "--ligand-p59-pdbqt", str(missing), "--ligand-p69-pdbqt", str(missing), "--autogrid4", str(missing), "--autogrid4-sha256", "0" * 64],
            ),
            (
                screening_verifier,
                ["--receptor-pdbqt", str(missing), "--ligand-p59-pdbqt", str(missing), "--ligand-p69-pdbqt", str(missing), "--autogrid4", str(missing), "--autogrid4-sha256", "0" * 64],
            ),
        ]
        for verifier, arguments in invocations:
            with self.subTest(verifier=verifier.VERIFIER_ID):
                stdout = io.StringIO()
                with mock.patch.object(
                    verifier,
                    "capture_source_bound_session",
                    side_effect=common.AcceptanceError("TEST_CAPTURE_FAILED", "failed"),
                ), mock.patch.object(verifier, "_load_backend_modules") as loader, mock.patch.object(
                    verifier,
                    "verify",
                ) as workflow:
                    with redirect_stdout(stdout):
                        exit_code = verifier.main(arguments)
                self.assertEqual(exit_code, 1)
                loader.assert_not_called()
                workflow.assert_not_called()
                result = json.loads(stdout.getvalue())
                self.assertFalse(result["ok"])
                self.assertEqual(result["provenance"]["binding_status"], "unbound")

    def test_import_origin_failure_never_enters_scientific_workflow(self) -> None:
        missing = self.root / "missing.pdbqt"
        arguments = [
            "--ligand-pdbqt",
            str(missing),
            "--autogrid4",
            str(missing),
            "--autogrid4-sha256",
            "0" * 64,
        ]
        stdout = io.StringIO()
        with mock.patch.object(
            flexible_verifier,
            "attach_loaded_import_origins",
            side_effect=common.AcceptanceError("TEST_ORIGIN_FAILED", "failed"),
        ), mock.patch.object(flexible_verifier, "verify") as workflow:
            with redirect_stdout(stdout):
                exit_code = flexible_verifier.main(arguments)
        self.assertEqual(exit_code, 1)
        workflow.assert_not_called()
        result = json.loads(stdout.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["provenance"]["binding_status"], "unbound")

    def test_recursive_fixture_files_are_source_bound(self) -> None:
        source_files = self.source_bound_payloads[0]["provenance"]["source_files"]
        fixture_paths = [
            value["path"]
            for value in source_files
            if value["role"] == "fixture_repository_file"
        ]
        self.assertEqual(
            fixture_paths,
            [
                "backend/tests/fixtures/scientific/flexible_1fpu/1fpu_receptorH.pdb",
                "backend/tests/fixtures/scientific/flexible_1fpu/expected_bad_residues.json",
            ],
        )

    def test_each_gate_failure_json_is_schema_v2_and_source_bound(self) -> None:
        missing = self.root / "missing.pdbqt"
        autogrid = self.root / "missing-autogrid4.exe"
        invocations = [
            (flexible_verifier, ["--ligand-pdbqt", str(missing), "--autogrid4", str(autogrid), "--autogrid4-sha256", "0" * 64]),
            (multiple_verifier, ["--receptor-pdbqt", str(missing), "--ligand-p59-pdbqt", str(missing), "--ligand-p69-pdbqt", str(missing), "--autogrid4", str(autogrid), "--autogrid4-sha256", "0" * 64]),
            (screening_verifier, ["--receptor-pdbqt", str(missing), "--ligand-p59-pdbqt", str(missing), "--ligand-p69-pdbqt", str(missing), "--autogrid4", str(autogrid), "--autogrid4-sha256", "0" * 64]),
        ]
        for verifier, arguments in invocations:
            with self.subTest(verifier=verifier.VERIFIER_ID):
                completed = subprocess.run(
                    [sys.executable, str(Path(verifier.__file__)), *arguments],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=60,
                )
                result = json.loads(completed.stdout)
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertEqual(result["schema_version"], 2)
                self.assertFalse(result["ok"])
                self.assertEqual(result["verifier_id"], verifier.VERIFIER_ID)
                self.assertEqual(result["provenance"]["binding_status"], "source_bound")

    def test_missing_duplicate_unknown_and_schema_are_rejected(self) -> None:
        with self.subTest(case="missing"):
            self.assertErrorCode(self._verify(self._payloads()[:2]), "AD4_EVIDENCE_MISSING_VERIFIER")
        payloads = self._payloads()
        with self.subTest(case="duplicate"):
            self.assertErrorCode(self._verify([payloads[0], copy.deepcopy(payloads[0]), payloads[1]]), "AD4_EVIDENCE_DUPLICATE_VERIFIER")
        payloads = self._payloads()
        payloads[2]["verifier_id"] = "dockstart_unknown_ad4_gate"
        with self.subTest(case="unknown"):
            self.assertErrorCode(self._verify(payloads), "AD4_EVIDENCE_UNKNOWN_VERIFIER")
        payloads = self._payloads()
        payloads[0]["schema_version"] = 3
        with self.subTest(case="schema"):
            self.assertErrorCode(self._verify(payloads), "AD4_EVIDENCE_SCHEMA_INVALID")

    def test_v1_with_provenance_and_v2_without_provenance_are_rejected(self) -> None:
        payloads = self._payloads()
        payloads[0]["schema_version"] = 1
        self.assertErrorCode(self._verify(payloads), "AD4_EVIDENCE_V1_PROVENANCE_FORBIDDEN")
        payloads = self._payloads()
        payloads[0].pop("provenance")
        self.assertErrorCode(self._verify(payloads), "AD4_EVIDENCE_V2_PROVENANCE_REQUIRED")

    def test_common_binder_rejects_schema_v1_gate_and_missing_session(self) -> None:
        contract = bundle.GATE_CONTRACTS[bundle.FLEXIBLE_ID]
        with self.assertRaises(common.AcceptanceError) as raised:
            common.bind_source_provenance(
                _flexible_payload(schema_version=1),
                repo_root=ROOT,
                verifier_path=contract["verifier_path"],
                fixture_manifest_paths=[contract["manifest_path"]],
            )
        self.assertEqual(raised.exception.code, "AD4_ACCEPTANCE_GATE_SCHEMA_REQUIRES_V2")
        with self.assertRaises(common.AcceptanceError) as raised:
            common.bind_source_provenance(
                _flexible_payload(),
                repo_root=ROOT,
                verifier_path=contract["verifier_path"],
                fixture_manifest_paths=[contract["manifest_path"]],
            )
        self.assertEqual(raised.exception.code, "AD4_ACCEPTANCE_SOURCE_SESSION_REQUIRED")
        session = _synthetic_imports_verified_session(bundle.FLEXIBLE_ID)
        with self.assertRaises(common.AcceptanceError) as raised:
            common.bind_source_provenance(
                _flexible_payload(),
                repo_root=ROOT,
                verifier_path=contract["verifier_path"],
                fixture_manifest_paths=[contract["manifest_path"]],
                source_session=session,
                validate_import_origins=False,
            )
        self.assertEqual(
            raised.exception.code,
            "AD4_ACCEPTANCE_GATE_IMPORT_VALIDATION_REQUIRED",
        )

    def test_payload_hash_detects_arbitrary_post_binding_tamper(self) -> None:
        mutations = [
            lambda values: values[0]["tools"]["autogrid4"].__setitem__("sha256", "0" * 64),
            lambda values: values[0]["run"]["command"].append("--fake"),
            lambda values: values[1]["maps"]["files"][0].__setitem__("sha256", "1" * 64),
            lambda values: values[2]["screening"]["attempts"][0]["output"].__setitem__("sha256", "2" * 64),
            lambda values: values[2]["screening"]["attempts"][1]["command"].append("--fake"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                payloads = self._payloads()
                mutate(payloads)
                self.assertErrorCode(self._verify(payloads), "AD4_EVIDENCE_PAYLOAD_IDENTITY_MISMATCH")

    def test_provenance_source_and_backend_tamper_are_rejected(self) -> None:
        payloads = self._payloads()
        payloads[0]["provenance"]["source_files"][0]["sha256"] = "0" * 64
        self.assertErrorCode(self._verify(payloads), "AD4_EVIDENCE_SOURCE_IDENTITY_MISMATCH")
        payloads = self._payloads()
        payloads[0]["provenance"]["backend_validation_tree"]["sha256"] = "0" * 64
        self.assertErrorCode(self._verify(payloads), "AD4_EVIDENCE_SOURCE_IDENTITY_MISMATCH")

    def test_import_origin_must_match_current_regular_source_file(self) -> None:
        payloads = self._payloads()
        guard = payloads[0]["provenance"]["execution_source_guard"]
        origins = guard["start_import_origins"]
        origins["modules"][0]["size_bytes"] += 1
        origins["modules"][0]["sha256"] = "a" * 64
        canonical = json.dumps(
            origins["modules"],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        origins["sha256"] = hashlib.sha256(canonical).hexdigest()
        guard["end_import_origins"] = copy.deepcopy(origins)
        self.assertErrorCode(
            self._verify(payloads),
            "AD4_EVIDENCE_SOURCE_IDENTITY_MISMATCH",
        )

    def test_source_guard_timestamp_tamper_is_rejected(self) -> None:
        payloads = self._payloads()
        payloads[0]["provenance"]["execution_source_guard"][
            "imports_verified_at_utc"
        ] = "9999-01-01T00:00:00Z"
        self.assertErrorCode(
            self._verify(payloads),
            "AD4_EVIDENCE_SOURCE_IDENTITY_MISMATCH",
        )

    def test_legacy_evidence_passes_default_and_fails_strict(self) -> None:
        result = self._verify(_legacy_payloads())
        self.assertTrue(result["ok"])
        self.assertEqual(result["binding_status"], "legacy_unbound")
        result = self._verify(_legacy_payloads(), require_source_bound=True)
        self.assertErrorCode(result, "AD4_EVIDENCE_SOURCE_BINDING_REQUIRED")

    def test_legacy_oracle_tampering_is_rejected(self) -> None:
        cases = [
            (0, lambda value: value["run"].__setitem__("best_affinity_kcal_mol", 0.0)),
            (1, lambda value: value["run"].__setitem__("per_member_scores_available", True)),
            (2, lambda value: value["screening"].__setitem__("failed_count", 0)),
            (2, lambda value: value["screening"]["ranking"]["ranked_item_ids"].reverse()),
        ]
        for gate_index, mutate in cases:
            with self.subTest(gate=gate_index):
                payloads = _legacy_payloads()
                mutate(payloads[gate_index])
                self.assertErrorCode(self._verify(payloads), "AD4_EVIDENCE_ORACLE_MISMATCH")

    def test_provenance_failure_becomes_structured_unbound_failure(self) -> None:
        payload = _flexible_payload()
        contract = bundle.GATE_CONTRACTS[bundle.FLEXIBLE_ID]
        session = _synthetic_imports_verified_session(bundle.FLEXIBLE_ID)
        with mock.patch.object(common, "git_worktree_identity", side_effect=common.AcceptanceError("TEST_PROVENANCE_FAILURE", "failure")):
            result = common.bind_source_provenance_or_error(
                payload,
                repo_root=ROOT,
                verifier_path=contract["verifier_path"],
                fixture_manifest_paths=[contract["manifest_path"]],
                source_session=session,
                expected_verifier_id=bundle.FLEXIBLE_ID,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "AD4_ACCEPTANCE_SOURCE_BINDING_FAILED")
        self.assertEqual(result["provenance_error"]["code"], "TEST_PROVENANCE_FAILURE")
        self.assertEqual(result["provenance"]["binding_status"], "unbound")

    def test_source_session_context_tamper_fails_closed(self) -> None:
        payload = _flexible_payload()
        contract = bundle.GATE_CONTRACTS[bundle.FLEXIBLE_ID]
        session = _synthetic_imports_verified_session(bundle.FLEXIBLE_ID)
        session["source_context"]["git"]["dirty"] = not session["source_context"]["git"]["dirty"]
        session["source_fingerprint"] = common.source_context_fingerprint(session["source_context"])
        result = common.bind_source_provenance_or_error(
            payload,
            repo_root=ROOT,
            verifier_path=contract["verifier_path"],
            fixture_manifest_paths=[contract["manifest_path"]],
            source_session=session,
            expected_verifier_id=bundle.FLEXIBLE_ID,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["provenance"]["binding_status"], "unbound")
        self.assertEqual(result["provenance_error"]["code"], "AD4_ACCEPTANCE_SOURCE_CHANGED_DURING_RUN")

    def test_import_origin_outside_expected_root_is_rejected(self) -> None:
        module = sys.modules["adapters"]
        original = module.__file__
        outside = self.root / "__init__.py"
        outside.write_text("", encoding="utf-8")
        try:
            module.__file__ = str(outside)
            with self.assertRaises(common.AcceptanceError) as raised:
                common.loaded_backend_module_origins(ROOT)
            self.assertEqual(raised.exception.code, "AD4_ACCEPTANCE_IMPORT_ORIGIN_MISMATCH")
        finally:
            module.__file__ = original

    def test_cli_output_input_collision_preserves_input(self) -> None:
        paths = self._write_payloads(_legacy_payloads())
        before = paths[0].read_bytes()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = bundle.main([*(str(path) for path in paths), "--output", str(paths[0])])
        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["error"]["code"], "AD4_EVIDENCE_OUTPUT_INPUT_COLLISION")
        self.assertEqual(paths[0].read_bytes(), before)

    def test_each_gate_output_collision_fails_before_work_and_preserves_input(self) -> None:
        scientific_input = self.root / "scientific-input.pdbqt"
        scientific_input.write_bytes(b"REMARK immutable input\n")
        invocations = [
            (
                flexible_verifier,
                [
                    "--ligand-pdbqt",
                    str(scientific_input),
                    "--autogrid4",
                    str(self.root / "autogrid4.exe"),
                    "--autogrid4-sha256",
                    "0" * 64,
                    "--output",
                    str(scientific_input),
                ],
            ),
            (
                multiple_verifier,
                [
                    "--receptor-pdbqt",
                    str(scientific_input),
                    "--ligand-p59-pdbqt",
                    str(self.root / "p59.pdbqt"),
                    "--ligand-p69-pdbqt",
                    str(self.root / "p69.pdbqt"),
                    "--autogrid4",
                    str(self.root / "autogrid4.exe"),
                    "--autogrid4-sha256",
                    "0" * 64,
                    "--output",
                    str(scientific_input),
                ],
            ),
            (
                screening_verifier,
                [
                    "--receptor-pdbqt",
                    str(scientific_input),
                    "--ligand-p59-pdbqt",
                    str(self.root / "p59.pdbqt"),
                    "--ligand-p69-pdbqt",
                    str(self.root / "p69.pdbqt"),
                    "--autogrid4",
                    str(self.root / "autogrid4.exe"),
                    "--autogrid4-sha256",
                    "0" * 64,
                    "--output",
                    str(scientific_input),
                ],
            ),
        ]
        for verifier, arguments in invocations:
            with self.subTest(verifier=verifier.VERIFIER_ID):
                before = scientific_input.read_bytes()
                stdout = io.StringIO()
                with mock.patch.object(verifier, "_capture_source_session") as capture:
                    with redirect_stdout(stdout):
                        exit_code = verifier.main(arguments)
                capture.assert_not_called()
                result = json.loads(stdout.getvalue())
                self.assertEqual(exit_code, 1)
                self.assertEqual(
                    result["error"]["code"],
                    "AD4_ACCEPTANCE_OUTPUT_INPUT_COLLISION",
                )
                self.assertEqual(result["provenance"]["binding_status"], "unbound")
                self.assertEqual(scientific_input.read_bytes(), before)

    def test_old_real_evidence_remains_read_only_legacy_compatible(self) -> None:
        evidence_root = ROOT / "output" / "qa" / "v0.14.1"
        names_and_hashes = [
            ("ad4-flexible-1fpu-attempt3.json", "9dd3a3e810c365b9d68e3575e51379fa4e7b3ce8a101a434dada9e0a11ce739a"),
            ("ad4-multiple-ligands-5x72-attempt2.json", "4cbc16810e4a65b154b684be5c2bee60b2ca54526a978ceb5f24aa876726b9f0"),
            ("ad4-serial-screening-attempt3.json", "af8baeb84ae34b414bc643897c0bd8d72558dc42af9023b59e1ad4290c0a8d75"),
        ]
        paths = [evidence_root / name for name, _ in names_and_hashes]
        if not all(path.is_file() for path in paths):
            self.skipTest("Local historical v0.14.1 evidence is not present.")
        before = [path.read_bytes() for path in paths]
        self.assertEqual(
            [hashlib.sha256(value).hexdigest() for value in before],
            [value for _, value in names_and_hashes],
        )
        default = bundle.verify_bundle(paths)
        strict = bundle.verify_bundle(paths, require_source_bound=True)
        self.assertTrue(default["ok"])
        self.assertEqual(default["binding_status"], "legacy_unbound")
        self.assertFalse(strict["ok"])
        self.assertEqual(strict["error"]["code"], "AD4_EVIDENCE_SOURCE_BINDING_REQUIRED")
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_atomic_write_failure_preserves_target_and_removes_temporary_file(self) -> None:
        output = self.root / "summary.json"
        output.write_text("original\n", encoding="utf-8")
        with mock.patch.object(common.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError), redirect_stdout(io.StringIO()):
                common.write_result({"ok": True}, output)
        self.assertEqual(output.read_text(encoding="utf-8"), "original\n")
        self.assertEqual(list(self.root.glob(f".{output.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
