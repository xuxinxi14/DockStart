from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.preparation import (  # noqa: E402
    MAX_LIGAND_PREPARATION_OPTIONS_JSON_BYTES,
    _publish_candidate_output,
    build_ligand_preparation_command_or_script,
    main,
    prepare_ligand_pdbqt,
)
from dockstart_core.macrocycle import (  # noqa: E402
    ANALYSIS_VERSION,
    CONTRACT_SCHEMA_VERSION,
    HYDROGEN_POLICY,
    MEEKO_API_PROFILE,
)
from dockstart_core.project import create_project  # noqa: E402


def _tool_status() -> dict:
    return {
        "ok": True,
        "project_dir": "",
        "tools": {
            "python": {
                "status": "ok",
                "version": "Python 3.11.0",
                "path": sys.executable,
                "source": "current_environment",
            },
            "rdkit": {"status": "ok", "version": "mock-rdkit"},
            "meeko": {
                "status": "ok",
                "version": "mock-meeko",
                "capabilities": {"ligand_preparation": {"status": "ok"}},
            },
        },
    }


def _pdbqt() -> str:
    return (
        "REMARK SMILES C1CCCCCCC1\n"
        "REMARK SMILES IDX 1 1 2 2\n"
        "ROOT\n"
        "ATOM      1  C1  LIG A   1       0.000   0.000   0.000"
        "  1.00  0.00     0.000 C\n"
        "ENDROOT\n"
        "TORSDOF 0\n"
    )


def _candidate_pdbqt() -> str:
    return (
        "REMARK SMILES C1CCCCCCC1\n"
        "REMARK SMILES IDX 1 1 2 2\n"
        "ROOT\n"
        "ATOM      1  C1  LIG A   1       0.000   0.000   0.000"
        "  1.00  0.00     0.000 C\n"
        "ATOM      2  G1  LIG A   1       1.000   0.000   0.000"
        "  1.00  0.00     0.000 G0\n"
        "ATOM      3  G2  LIG A   1       0.000   1.000   0.000"
        "  1.00  0.00     0.000 G1\n"
        "ENDROOT\n"
        "TORSDOF 1\n"
    )


CONFIRMATION_SHA256 = "c" * 64
ATOM_TABLE_SHA256 = "a" * 64
BOND_TOPOLOGY = [
    {
        "atom_indices_zero_based": [0, 1],
        "bond_type": "SINGLE",
        "bond_order": 1.0,
        "is_aromatic": False,
        "is_conjugated": False,
        "stereo": "STEREONONE",
        "stereo_atom_indices_zero_based": [],
    }
]
BOND_TOPOLOGY_SHA256 = hashlib.sha256(
    json.dumps(
        BOND_TOPOLOGY,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()
ATOM_INDEXING = {
    "hydrogen_policy": HYDROGEN_POLICY,
    "source_atom_count": 2,
    "prepared_atom_count": 2,
    "source_heavy_atom_indices_zero_based": [0, 1],
    "source_to_prepared_indices_zero_based": [0, 1],
    "added_hydrogen_indices_zero_based": [],
    "source_indices_preserved": True,
}


class MacrocyclePreparationIntegrationTests(unittest.TestCase):
    def _project(self, parent: str) -> Path:
        created = create_project("macrocycle_prep", parent)
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])
        raw = project_dir / "raw" / "ligand.sdf"
        raw.write_text("mock sdf\n", encoding="utf-8")
        project_json = project_dir / "project.json"
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data["ligand"]["raw_file"] = "raw/ligand.sdf"
        project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return project_dir

    def _reviewed_plan(
        self,
        *,
        selection_mode: str,
        observed: dict[str, object],
    ):
        exact_bonds = [[0, 1]] if selection_mode == "candidate" else []
        candidate_id = "candidate_001" if selection_mode == "candidate" else ""

        def build_plan(
            project_dir: str,
            python_executable: str,
            output_pdbqt: str | Path,
            *,
            record_dir: str | Path,
            expected_review_id: str | None = None,
            expected_confirmation_sha256: str | None = None,
            target_lock_held: bool = False,
        ) -> dict:
            root = Path(project_dir)
            record = Path(record_dir)
            frozen = record / "macrocycle_input.sdf"
            raw_bytes = (root / "raw" / "ligand.sdf").read_bytes()
            frozen.write_bytes(raw_bytes)
            frozen_sha = hashlib.sha256(raw_bytes).hexdigest()
            contract = {
                "protocol_id": "meeko_macrocycle",
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "analysis_version": ANALYSIS_VERSION,
                "meeko_api_profile": MEEKO_API_PROFILE,
                "hydrogen_policy": HYDROGEN_POLICY,
                "review_id": "review_001",
                "confirmation_sha256": CONFIRMATION_SHA256,
                "selection_mode": selection_mode,
                "candidate_id": candidate_id,
                "exact_bonds": exact_bonds,
                "atom_table_sha256": ATOM_TABLE_SHA256,
                "atom_indexing": ATOM_INDEXING,
                "bond_topology": BOND_TOPOLOGY,
                "bond_topology_sha256": BOND_TOPOLOGY_SHA256,
                "tool_versions": {
                    "python": "Python 3.11.0",
                    "rdkit": "mock-rdkit",
                    "meeko": "mock-meeko",
                },
                "runtime_input": {
                    "relative_path": frozen.relative_to(root).as_posix(),
                    "sha256": frozen_sha,
                    "size_bytes": len(raw_bytes),
                },
            }
            contract_file = record / "macrocycle_contract.json"
            contract_file.write_text(
                json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            evidence_file = record / "macrocycle_evidence.json"
            observed.update(
                {
                    "target_lock_held": target_lock_held,
                    "expected_review_id": expected_review_id,
                    "expected_confirmation_sha256": expected_confirmation_sha256,
                    "frozen_input": str(frozen),
                }
            )
            return {
                "ok": True,
                "project_dir": str(root),
                "protocol": "meeko_macrocycle",
                "argv": [
                    str(python_executable),
                    "fake-reviewed-worker",
                    "--input",
                    str(frozen),
                    "--output",
                    str(output_pdbqt),
                    "--evidence",
                    str(evidence_file),
                ],
                "contract": contract,
                "contract_file": contract_file.relative_to(root).as_posix(),
                "contract_sha256": hashlib.sha256(contract_file.read_bytes()).hexdigest(),
                "input_snapshot_file": frozen.relative_to(root).as_posix(),
                "evidence_file": evidence_file.relative_to(root).as_posix(),
                "expected_output_evidence": {
                    "selection_mode": selection_mode,
                    "candidate_id": candidate_id,
                    "exact_bonds": exact_bonds,
                    "atom_table_sha256": ATOM_TABLE_SHA256,
                    "bond_topology_sha256": BOND_TOPOLOGY_SHA256,
                    "confirmation_sha256": CONFIRMATION_SHA256,
                },
                "error": None,
            }

        return build_plan

    def _reviewed_run(
        self,
        *,
        selection_mode: str,
        mutate_evidence=None,
        write_evidence: bool = True,
    ):
        exact_bonds = [[0, 1]] if selection_mode == "candidate" else []
        candidate_id = "candidate_001" if selection_mode == "candidate" else ""

        def run(
            command: list[str],
            cwd: str | Path,
            timeout: int = 300,
        ) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            output = Path(command[command.index("--output") + 1])
            evidence_file = Path(command[command.index("--evidence") + 1])
            output.write_text(
                _candidate_pdbqt() if selection_mode == "candidate" else _pdbqt(),
                encoding="utf-8",
            )
            evidence = {
                "ok": True,
                "protocol_id": "meeko_macrocycle",
                "output_file": str(output),
                "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "output_size_bytes": output.stat().st_size,
                "selection_mode": selection_mode,
                "candidate_id": candidate_id,
                "expected_bonds": exact_bonds,
                "actual_bonds": exact_bonds,
                "glue_pseudo_atom_count": 2 * len(exact_bonds),
                "atom_table_sha256": ATOM_TABLE_SHA256,
                "bond_topology_sha256": BOND_TOPOLOGY_SHA256,
                "hydrogen_policy": HYDROGEN_POLICY,
                "atom_indexing": ATOM_INDEXING,
                "review_id": "review_001",
                "confirmation_sha256": CONFIRMATION_SHA256,
                "meeko_version": "mock-meeko",
                "rdkit_version": "mock-rdkit",
                "error": None,
            }
            if mutate_evidence is not None:
                mutate_evidence(evidence)
            if write_evidence:
                evidence_file.write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(command, 0, stdout="reviewed prepared", stderr="")

        return run

    def test_omitted_options_keep_the_legacy_script_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            with patch(
                "dockstart_core.preparation.get_preparation_tool_status",
                return_value=_tool_status(),
            ):
                built = build_ligand_preparation_command_or_script(str(project_dir))

        self.assertTrue(built["ok"], built)
        self.assertTrue(built["script_file"].endswith("prepare_ligand_rdkit_meeko.py"))
        self.assertNotIn("protocol", built)
        self.assertNotIn("options", built)
        self.assertNotIn("meeko.cli.mk_prepare_ligand", built["command"])

    def test_legacy_macrocycle_modes_cannot_create_new_preparation_records(self) -> None:
        for mode in ("auto", "rigid"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                project_dir = self._project(temp_dir)
                result = prepare_ligand_pdbqt(
                    str(project_dir),
                    options={
                        "protocol": "meeko_macrocycle",
                        "macrocycle": {"mode": mode},
                    },
                )
                records = list(
                    (project_dir / "preparation").glob("ligand_*")
                )

            self.assertFalse(result["ok"])
            self.assertEqual(
                result["error"]["code"],
                "MACROCYCLE_LEGACY_MODE_DISABLED",
            )
            self.assertEqual(records, [])

    def test_invalid_macrocycle_options_fail_before_creating_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            result = prepare_ligand_pdbqt(
                str(project_dir),
                options={
                    "protocol": "meeko_macrocycle",
                    "macrocycle": {"mode": "unsupported"},
                },
            )
            records = list((project_dir / "preparation").glob("ligand_*"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_MACROCYCLE_MODE")
        self.assertEqual(records, [])

    def test_reviewed_options_reject_frontend_supplied_bonds_before_record_creation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            result = prepare_ligand_pdbqt(
                str(project_dir),
                options={
                    "protocol": "meeko_macrocycle",
                    "macrocycle": {
                        "mode": "reviewed",
                        "review_id": "review_001",
                        "confirmation_sha256": CONFIRMATION_SHA256,
                        "exact_bonds": [[0, 1]],
                    },
                },
            )
            records = list((project_dir / "preparation").glob("ligand_*"))

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "MACROCYCLE_REVIEWED_OPTIONS_UNKNOWN",
        )
        self.assertEqual(records, [])

    def test_reviewed_candidate_uses_frozen_input_and_publishes_only_with_matching_evidence(
        self,
    ) -> None:
        observed: dict[str, object] = {}
        options = {
            "protocol": "meeko_macrocycle",
            "macrocycle": {
                "mode": "reviewed",
                "review_id": "review_001",
                "confirmation_sha256": CONFIRMATION_SHA256,
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            with (
                patch(
                    "dockstart_core.preparation.get_preparation_tool_status",
                    return_value=_tool_status(),
                ),
                patch(
                    "dockstart_core.preparation.build_reviewed_preparation_plan",
                    side_effect=self._reviewed_plan(
                        selection_mode="candidate",
                        observed=observed,
                    ),
                ),
                patch(
                    "adapters.meeko_adapter.run_preparation_command",
                    side_effect=self._reviewed_run(selection_mode="candidate"),
                ),
            ):
                result = prepare_ligand_pdbqt(str(project_dir), options=options)
            metadata = json.loads(
                (project_dir / result["metadata_file"]).read_text(encoding="utf-8")
            )
            prepared = project_dir / "prepared" / "ligand.pdbqt"
            prepared_exists = prepared.is_file()

        self.assertTrue(result["ok"], result)
        self.assertTrue(observed["target_lock_held"])
        self.assertEqual(observed["expected_review_id"], "review_001")
        self.assertEqual(
            observed["expected_confirmation_sha256"],
            CONFIRMATION_SHA256,
        )
        self.assertIn(
            "preparation/ligand_001/macrocycle_input.sdf",
            str(observed["frozen_input"]).replace("\\", "/"),
        )
        self.assertEqual(metadata["protocol_mode"], "reviewed")
        self.assertEqual(
            metadata["macrocycle_contract"]["selection_mode"],
            "candidate",
        )
        self.assertEqual(
            metadata["macrocycle_evidence_file"],
            "preparation/ligand_001/macrocycle_evidence.json",
        )
        self.assertTrue(metadata["protocol_evidence"]["ok"])
        self.assertEqual(
            len(metadata["protocol_evidence"]["inspection"]["glue_pseudo_atoms"]),
            2,
        )
        self.assertTrue(metadata["published"])
        self.assertTrue(prepared_exists)

    def test_reviewed_rigid_requires_no_breaks_and_no_glue_atoms(self) -> None:
        observed: dict[str, object] = {}
        options = {
            "protocol": "meeko_macrocycle",
            "macrocycle": {
                "mode": "reviewed",
                "review_id": "review_001",
                "confirmation_sha256": CONFIRMATION_SHA256,
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            with (
                patch(
                    "dockstart_core.preparation.get_preparation_tool_status",
                    return_value=_tool_status(),
                ),
                patch(
                    "dockstart_core.preparation.build_reviewed_preparation_plan",
                    side_effect=self._reviewed_plan(
                        selection_mode="rigid",
                        observed=observed,
                    ),
                ),
                patch(
                    "adapters.meeko_adapter.run_preparation_command",
                    side_effect=self._reviewed_run(selection_mode="rigid"),
                ),
            ):
                result = prepare_ligand_pdbqt(str(project_dir), options=options)
            metadata = json.loads(
                (project_dir / result["metadata_file"]).read_text(encoding="utf-8")
            )

        self.assertTrue(result["ok"], result)
        evidence = metadata["protocol_evidence"]["evidence"]
        self.assertEqual(evidence["actual_bonds"], [])
        self.assertEqual(evidence["glue_pseudo_atom_count"], 0)
        self.assertEqual(
            metadata["protocol_evidence"]["inspection"]["glue_pseudo_atoms"],
            [],
        )

    def test_reviewed_evidence_mismatch_never_replaces_existing_prepared_file(self) -> None:
        observed: dict[str, object] = {}
        options = {
            "protocol": "meeko_macrocycle",
            "macrocycle": {
                "mode": "reviewed",
                "review_id": "review_001",
                "confirmation_sha256": CONFIRMATION_SHA256,
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            prepared = project_dir / "prepared" / "ligand.pdbqt"
            prepared.write_text("previous prepared output\n", encoding="utf-8")

            def corrupt(evidence: dict) -> None:
                evidence["actual_bonds"] = [[1, 2]]
                evidence["output_sha256"] = "0" * 64
                evidence["bond_topology_sha256"] = "1" * 64
                evidence["rdkit_version"] = "different-rdkit"

            with (
                patch(
                    "dockstart_core.preparation.get_preparation_tool_status",
                    return_value=_tool_status(),
                ),
                patch(
                    "dockstart_core.preparation.build_reviewed_preparation_plan",
                    side_effect=self._reviewed_plan(
                        selection_mode="candidate",
                        observed=observed,
                    ),
                ),
                patch(
                    "adapters.meeko_adapter.run_preparation_command",
                    side_effect=self._reviewed_run(
                        selection_mode="candidate",
                        mutate_evidence=corrupt,
                    ),
                ),
            ):
                result = prepare_ligand_pdbqt(
                    str(project_dir),
                    overwrite=True,
                    options=options,
                )
            metadata = json.loads(
                (project_dir / result["metadata_file"]).read_text(encoding="utf-8")
            )
            remaining = prepared.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "MACROCYCLE_EVIDENCE_GATE_FAILED")
        self.assertEqual(remaining, "previous prepared output\n")
        self.assertFalse(metadata["published"])
        self.assertFalse(metadata["protocol_evidence"]["ok"])
        issue_codes = {
            item["code"] for item in metadata["protocol_evidence"]["issues"]
        }
        self.assertIn("MACROCYCLE_BOND_EVIDENCE_MISMATCH", issue_codes)
        self.assertIn("MACROCYCLE_OUTPUT_EVIDENCE_MISMATCH", issue_codes)
        self.assertIn("MACROCYCLE_EVIDENCE_FIELD_MISMATCH", issue_codes)
        self.assertIn("MACROCYCLE_TOOL_VERSION_EVIDENCE_MISMATCH", issue_codes)

    def test_reviewed_missing_evidence_never_publishes_candidate(self) -> None:
        observed: dict[str, object] = {}
        options = {
            "protocol": "meeko_macrocycle",
            "macrocycle": {
                "mode": "reviewed",
                "review_id": "review_001",
                "confirmation_sha256": CONFIRMATION_SHA256,
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            with (
                patch(
                    "dockstart_core.preparation.get_preparation_tool_status",
                    return_value=_tool_status(),
                ),
                patch(
                    "dockstart_core.preparation.build_reviewed_preparation_plan",
                    side_effect=self._reviewed_plan(
                        selection_mode="rigid",
                        observed=observed,
                    ),
                ),
                patch(
                    "adapters.meeko_adapter.run_preparation_command",
                    side_effect=self._reviewed_run(
                        selection_mode="rigid",
                        write_evidence=False,
                    ),
                ),
            ):
                result = prepare_ligand_pdbqt(str(project_dir), options=options)
            metadata = json.loads(
                (project_dir / result["metadata_file"]).read_text(encoding="utf-8")
            )
            prepared = project_dir / "prepared" / "ligand.pdbqt"
            prepared_exists = prepared.exists()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "MACROCYCLE_EVIDENCE_GATE_FAILED")
        self.assertFalse(prepared_exists)
        self.assertFalse(metadata["protocol_evidence"]["ok"])
        self.assertIn(
            "MACROCYCLE_EVIDENCE_READ_FAILED",
            {item["code"] for item in metadata["protocol_evidence"]["issues"]},
        )

    def test_reviewed_candidate_changed_after_gate_is_not_published(self) -> None:
        observed: dict[str, object] = {}
        options = {
            "protocol": "meeko_macrocycle",
            "macrocycle": {
                "mode": "reviewed",
                "review_id": "review_001",
                "confirmation_sha256": CONFIRMATION_SHA256,
            },
        }
        real_publish = _publish_candidate_output

        def replace_then_publish(
            candidate: Path,
            destination: Path,
            project_path: Path | None = None,
            *,
            expected_snapshot=None,
        ) -> None:
            candidate.write_text("REMARK replaced after evidence gate\n", encoding="utf-8")
            real_publish(
                candidate,
                destination,
                project_path,
                expected_snapshot=expected_snapshot,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            prepared = project_dir / "prepared" / "ligand.pdbqt"
            prepared.write_text("previous prepared output\n", encoding="utf-8")
            with (
                patch(
                    "dockstart_core.preparation.get_preparation_tool_status",
                    return_value=_tool_status(),
                ),
                patch(
                    "dockstart_core.preparation.build_reviewed_preparation_plan",
                    side_effect=self._reviewed_plan(
                        selection_mode="candidate",
                        observed=observed,
                    ),
                ),
                patch(
                    "adapters.meeko_adapter.run_preparation_command",
                    side_effect=self._reviewed_run(selection_mode="candidate"),
                ),
                patch(
                    "dockstart_core.preparation._publish_candidate_output",
                    side_effect=replace_then_publish,
                ),
            ):
                result = prepare_ligand_pdbqt(
                    str(project_dir),
                    overwrite=True,
                    options=options,
                )
            metadata = json.loads(
                (project_dir / result["metadata_file"]).read_text(encoding="utf-8")
            )
            remaining = prepared.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "MACROCYCLE_CANDIDATE_CHANGED_BEFORE_PUBLISH",
        )
        self.assertEqual(remaining, "previous prepared output\n")
        self.assertFalse(metadata["published"])
        self.assertTrue(metadata["protocol_evidence"]["ok"])


class MacrocyclePreparationCliTests(unittest.TestCase):
    def test_cli_passes_size_limited_options_json_to_prepare(self) -> None:
        options = {
            "protocol": "meeko_macrocycle",
            "macrocycle": {"mode": "auto", "min_ring_size": 8},
        }
        stdout = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["preparation", "prepare-ligand", "project", "false", json.dumps(options)],
            ),
            patch(
                "dockstart_core.preparation.prepare_ligand_pdbqt",
                return_value={"ok": True, "project": None},
            ) as prepared,
            redirect_stdout(stdout),
        ):
            main()

        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        prepared.assert_called_once_with("project", overwrite=False, options=options)

    def test_cli_rejects_invalid_or_oversized_options_json(self) -> None:
        for raw, code in (
            ("{invalid", "LIGAND_PREPARATION_OPTIONS_JSON_INVALID"),
            (
                '"' + ("x" * MAX_LIGAND_PREPARATION_OPTIONS_JSON_BYTES) + '"',
                "LIGAND_PREPARATION_OPTIONS_TOO_LARGE",
            ),
        ):
            with self.subTest(code=code):
                stdout = io.StringIO()
                with (
                    patch.object(
                        sys,
                        "argv",
                        ["preparation", "prepare-ligand", "project", "false", raw],
                    ),
                    redirect_stdout(stdout),
                ):
                    main()
                payload = json.loads(stdout.getvalue())
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], code)


if __name__ == "__main__":
    unittest.main()
