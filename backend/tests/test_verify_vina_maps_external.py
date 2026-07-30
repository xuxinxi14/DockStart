from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "verify_vina_maps_external.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_vina_maps_external",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class VinaMapsExternalVerifierTests(unittest.TestCase):
    def test_fixed_tool_and_real_result_oracles_are_review_anchored(
        self,
    ) -> None:
        self.assertEqual(
            VERIFY.DEFAULT_VINA_CONTRACT["sha256"],
            "e0c4b2715e0c1a74f6e92d0f3be0328ac97542eafbc111e6b1efad897a73cce5",
        )
        self.assertEqual(
            VERIFY.DEFAULT_VINA_CONTRACT["help_advanced_sha256"],
            "fc85023362623eaf979e262257f8b949019b5ae235a106ca9035a22f3dc368ab",
        )
        self.assertEqual(
            VERIFY.DEFAULT_ACCEPTANCE_ORACLE["vina"]["maps_payload_sha256"],
            "945daff3d8de04b89436ac52890208e7d3027fa0709ed90b80e1fb13a7dabe8c",
        )
        self.assertEqual(
            VERIFY.DEFAULT_ACCEPTANCE_ORACLE["vina"]["output_sha256"],
            "f2be3cd50c5a4eee1ac96f1cad9e6dfae03e4964a47e439d6a9543f7db33b962",
        )
        self.assertEqual(
            VERIFY.DEFAULT_ACCEPTANCE_ORACLE["vinardo"][
                "maps_payload_sha256"
            ],
            "c6044ff772ee5f58ac01954167d5111c7160a3cf07368fa459b34f8c039636d9",
        )
        self.assertEqual(
            VERIFY.DEFAULT_ACCEPTANCE_ORACLE["vinardo"]["output_sha256"],
            "94dd7082833b215bbace224a13a2b3753f0a0590d3a6bf1b9151f02365a10937",
        )

    def test_pinned_default_input_contract_matches_repository_bytes(
        self,
    ) -> None:
        profile = VERIFY._input_acceptance_profile(
            VERIFY.DEFAULT_RECEPTOR.resolve(strict=True),
            VERIFY.DEFAULT_LIGAND.resolve(strict=True),
        )
        self.assertEqual(profile["kind"], "pinned_default_acceptance")
        self.assertTrue(profile["counts_as_default_acceptance"])
        self.assertEqual(
            profile["receptor"]["size_bytes"],
            VERIFY.DEFAULT_INPUT_CONTRACT["receptor"]["size_bytes"],
        )
        self.assertEqual(
            profile["receptor"]["sha256"],
            VERIFY.DEFAULT_INPUT_CONTRACT["receptor"]["sha256"],
        )
        self.assertEqual(
            profile["ligand"]["size_bytes"],
            VERIFY.DEFAULT_INPUT_CONTRACT["ligand"]["size_bytes"],
        )
        self.assertEqual(
            profile["ligand"]["sha256"],
            VERIFY.DEFAULT_INPUT_CONTRACT["ligand"]["sha256"],
        )

    def test_mutated_default_fixture_bytes_are_blocked_by_pinned_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mutated = Path(temporary) / "receptor.pdbqt"
            payload = bytearray(VERIFY.DEFAULT_RECEPTOR.read_bytes())
            payload[-1] = (
                ord("X") if payload[-1] != ord("X") else ord("Y")
            )
            mutated.write_bytes(payload)
            with self.assertRaises(
                VERIFY.VinaMapsExternalVerificationError
            ) as raised:
                VERIFY._pinned_default_file_evidence(
                    mutated,
                    role="receptor",
                    contract=VERIFY.DEFAULT_INPUT_CONTRACT["receptor"],
                )
        self.assertEqual(
            raised.exception.code,
            "VINA_MAPS_VERIFIER_DEFAULT_FIXTURE_MISMATCH",
        )

    def test_private_input_snapshot_rejects_initial_identity_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdbqt"
            destination = root / "snapshot" / "input.pdbqt"
            source.write_bytes(b"fixed-input\n")
            contract = VERIFY._file_evidence(source)
            source.write_bytes(b"drift-input\n")
            with self.assertRaises(
                VERIFY.VinaMapsExternalVerificationError
            ) as raised:
                VERIFY._snapshot_file(
                    source,
                    destination,
                    contract,
                    label="test input",
                )
        self.assertEqual(
            raised.exception.code,
            "VINA_MAPS_VERIFIER_FILE_CONTRACT_MISMATCH",
        )

    def test_audited_runner_rejects_binary_change_during_process(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "vina.exe"
            executable.write_bytes(b"fixed-tool")
            contract = VERIFY._file_evidence(executable)

            def drift_runner(
                _command,
                _cwd,
                stdout_path,
                stderr_path,
                log_path,
            ):
                Path(stdout_path).write_text("stdout", encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text("stdout", encoding="utf-8")
                executable.write_bytes(b"drift-tool")
                return {
                    "ok": True,
                    "pid": 123,
                    "exit_code": 0,
                    "error": "",
                }

            auditor = VERIFY._AuditedVinaRunner(
                executable,
                contract,
                run_impl=drift_runner,
            )
            with self.assertRaises(
                VERIFY.VinaMapsExternalVerificationError
            ) as raised:
                auditor(
                    [str(executable), "--write_maps", "receptor"],
                    root,
                    root / "stdout.txt",
                    root / "stderr.txt",
                    root / "log.txt",
                )
        self.assertEqual(
            raised.exception.code,
            "VINA_MAPS_VERIFIER_FILE_CONTRACT_MISMATCH",
        )

    def test_custom_paths_are_probe_only_even_when_bytes_match_defaults(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receptor = root / "copied_receptor.pdbqt"
            ligand = root / "copied_ligand.pdbqt"
            receptor.write_bytes(VERIFY.DEFAULT_RECEPTOR.read_bytes())
            ligand.write_bytes(VERIFY.DEFAULT_LIGAND.read_bytes())
            profile = VERIFY._input_acceptance_profile(receptor, ligand)
        self.assertEqual(profile["kind"], "custom_input_probe")
        self.assertFalse(profile["counts_as_default_acceptance"])
        self.assertIn("cannot replace", profile["qualification"])

    def test_same_size_corruption_changes_hash_and_remains_parseable_text(
        self,
    ) -> None:
        original = (
            b"SPACING 0.375\n"
            b"NELEMENTS 2 2 2\n"
            b"CENTER 0 0 0\n"
            b"0.000\n"
        )
        mutated = VERIFY._mutate_map_bytes(original)
        self.assertEqual(len(mutated), len(original))
        self.assertNotEqual(mutated, original)
        mutated.decode("ascii")

    def test_independent_map_oracle_rejects_self_consistent_wrong_bytes(
        self,
    ) -> None:
        oracle = VERIFY.DEFAULT_ACCEPTANCE_ORACLE["vina"]
        evidence = {
            "payload_sha256": oracle["maps_payload_sha256"],
            "maps": [
                {
                    "name": name,
                    "atom_type": record["atom_type"],
                    "size_bytes": record["size_bytes"],
                    "sha256": record["sha256"],
                }
                for name, record in oracle["maps"].items()
            ],
            "write_maps_process": {
                "audit": {
                    "stdout": {
                        "sha256": oracle["generation_stdout_sha256"],
                    }
                }
            },
        }
        VERIFY._assert_generation_oracle(evidence, scoring="vina")
        evidence["maps"][0]["sha256"] = "0" * 64
        with self.assertRaises(
            VERIFY.VinaMapsExternalVerificationError
        ) as raised:
            VERIFY._assert_generation_oracle(evidence, scoring="vina")
        self.assertEqual(
            raised.exception.code,
            "VINA_MAPS_VERIFIER_GENERATION_ORACLE_MISMATCH",
        )

    def test_box_validation_rejects_non_finite_and_non_positive_sizes(
        self,
    ) -> None:
        for patch in (
            {"center_x": float("nan")},
            {"size_z": 0},
            {"size_y": -1},
        ):
            with self.subTest(patch=patch):
                with self.assertRaises(
                    VERIFY.VinaMapsExternalVerificationError
                ) as raised:
                    VERIFY._validated_box(
                        {
                            **VERIFY.DEFAULT_BOX,
                            **patch,
                        }
                    )
                self.assertEqual(
                    raised.exception.code,
                    "VINA_MAPS_VERIFIER_BOX_INVALID",
                )

    def test_real_vina_127_replays_both_scoring_workflows_and_retry(
        self,
    ) -> None:
        previous_settings = os.environ.get(VERIFY.SETTINGS_ENV_VAR)
        previous_resources = os.environ.get(VERIFY.RESOURCE_DIR_ENV_VAR)
        result = VERIFY.verify_vina_maps_external()

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["verifier_id"], VERIFY.VERIFIER_ID)
        self.assertFalse(result["network_or_download_used"])
        self.assertTrue(
            result["acceptance"]["default_fixture_gate_satisfied"]
        )
        self.assertEqual(
            result["acceptance"]["input_profile"],
            "pinned_default_acceptance",
        )
        self.assertTrue(
            result["coverage"][
                "real_vina_nonzero_generation_exit_and_retry"
            ]
        )
        self.assertFalse(
            result["coverage"][
                "real_concurrent_vina_generation_processes"
            ]
        )
        self.assertRegex(result["evidence_sha256"], r"^[0-9a-f]{64}$")
        workflows = result["steps"]["scoring_workflows"]["workflows"]
        self.assertEqual(set(workflows), {"vina", "vinardo"})
        for scoring, workflow in workflows.items():
            generation = workflow["generation"]
            write_command = generation["write_maps_process"]["command"]
            self.assertIn("--write_maps", write_command)
            self.assertEqual(
                write_command[write_command.index("--scoring") + 1],
                scoring,
            )
            write_audit = generation["write_maps_process"]["audit"]
            self.assertGreater(write_audit["pid"], 0)
            self.assertEqual(write_audit["exit_code"], 0)
            self.assertEqual(
                write_audit["vina_before"]["sha256"],
                VERIFY.DEFAULT_VINA_CONTRACT["sha256"],
            )
            self.assertEqual(
                write_audit["vina_after"]["sha256"],
                VERIFY.DEFAULT_VINA_CONTRACT["sha256"],
            )
            probe_command = workflow["activation"]["probe"]["command"]
            self.assertIn("--maps", probe_command)
            self.assertIn("--score_only", probe_command)
            self.assertGreater(
                workflow["activation"]["probe"]["audit"]["pid"],
                0,
            )
            dock = workflow["docking"]
            self.assertIn("--maps", dock["prepared_command"])
            self.assertNotIn("--receptor", dock["prepared_command"])
            self.assertEqual(dock["process"]["exit_code"], 0)
            self.assertGreater(dock["process"]["pid"], 0)
            self.assertTrue(
                dock["process"]["process_identity"]["creation_token"]
            )
            self.assertTrue(dock["fixed_oracle"]["enforced"])
            self.assertTrue(dock["scores"])
            for artifact in dock["artifacts"].values():
                self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")

        process_failure = result["steps"][
            "real_process_failure_and_retry"
        ]
        self.assertTrue(process_failure["ok"])
        self.assertGreater(
            process_failure["failure"]["audited_process"]["pid"],
            0,
        )
        self.assertEqual(
            process_failure["failure"]["error_code"],
            "VINA_MAPS_GENERATION_FAILED",
        )
        self.assertNotEqual(process_failure["failure"]["exit_code"], 0)
        self.assertEqual(
            process_failure["failure"]["published_sets_after_failure"],
            [],
        )
        self.assertEqual(
            process_failure["failure"]["staging_entries_after_failure"],
            [],
        )
        self.assertEqual(
            process_failure["retry"]["generation"]["map_set_id"],
            process_failure["failure"][
                "map_set_id_reserved_but_not_published"
            ],
        )
        self.assertEqual(
            process_failure["retry"]["docking"]["process"]["exit_code"],
            0,
        )

        gate = result["steps"]["corruption_and_retry"]
        self.assertTrue(gate["ok"])
        self.assertEqual(
            set(gate["fail_closed"]["api_error_codes"].values()),
            {"VINA_MAPS_VALIDATION_FAILED"},
        )
        self.assertFalse(gate["fail_closed"]["new_run_allocated"])
        self.assertFalse(
            gate["fail_closed"][
                "external_process_started_by_rejected_gates"
            ]
        )
        audited = result["steps"]["audited_adapter_processes"]
        self.assertEqual(audited["event_count"], 9)
        self.assertTrue(
            all(event["pid"] > 0 for event in audited["events"])
        )
        self.assertEqual(
            gate["retry"]["active_map_set_id"],
            gate["retry"]["generation"]["map_set_id"],
        )
        self.assertEqual(
            gate["retry"]["docking"]["process"]["exit_code"],
            0,
        )
        self.assertTrue(result["environment"]["work_directory_cleaned"])
        self.assertEqual(
            os.environ.get(VERIFY.SETTINGS_ENV_VAR),
            previous_settings,
        )
        self.assertEqual(
            os.environ.get(VERIFY.RESOURCE_DIR_ENV_VAR),
            previous_resources,
        )

    def test_custom_input_probe_completes_but_cannot_exit_as_acceptance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receptor = root / "copied_receptor.pdbqt"
            ligand = root / "copied_ligand.pdbqt"
            receptor.write_bytes(VERIFY.DEFAULT_RECEPTOR.read_bytes())
            ligand.write_bytes(VERIFY.DEFAULT_LIGAND.read_bytes())
            result = VERIFY.verify_vina_maps_external(
                receptor_pdbqt=receptor,
                ligand_pdbqt=ligand,
            )
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["acceptance"]["workflow_completed"])
        self.assertFalse(result["acceptance"]["formal_acceptance"])
        self.assertEqual(
            result["error"]["code"],
            "VINA_MAPS_VERIFIER_CUSTOM_PROBE_NOT_ACCEPTANCE",
        )
        with (
            mock.patch.object(
                VERIFY,
                "verify_vina_maps_external",
                return_value=result,
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(VERIFY.main([]), 1)

    def test_missing_vina_is_explicit_failure_not_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-vina.exe"
            result = VERIFY.verify_vina_maps_external(
                vina_executable=missing,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "VINA_MAPS_VERIFIER_FILE_MISSING",
        )
        self.assertTrue(result["environment"]["work_directory_cleaned"])


if __name__ == "__main__":
    unittest.main()
