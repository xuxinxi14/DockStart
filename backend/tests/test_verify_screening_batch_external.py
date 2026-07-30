from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.screening import (  # noqa: E402
    create_screening,
    request_screening_cancel,
    resume_screening,
    run_screening,
)


SPEC = importlib.util.spec_from_file_location(
    "verify_screening_batch_external",
    ROOT / "scripts" / "verify_screening_batch_external.py",
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


FAKE_LOG = """AutoDock Vina 1.2.7

mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       -1.000          0          0
"""
FAKE_OUTPUT = """MODEL 1
REMARK VINA RESULT:    -1.000      0.000      0.000
ENDMDL
"""


class ScreeningBatchExternalVerifierTests(unittest.TestCase):
    def test_fixed_contract_and_default_scale_are_literal(self) -> None:
        self.assertEqual(VERIFY.DEFAULT_LIGAND_COUNT, 100)
        self.assertEqual(VERIFY.DEFAULT_CANCEL_AFTER, 20)
        self.assertEqual(VERIFY.MAX_LIGAND_COUNT, 500)
        self.assertEqual(
            VERIFY.DEFAULT_VINA_CONTRACT["sha256"],
            "e0c4b2715e0c1a74f6e92d0f3be0328ac97542eafbc111e6b1efad897a73cce5",
        )
        self.assertEqual(
            VERIFY.DEFAULT_RECEPTOR_CONTRACT["sha256"],
            "2ef52a38914a61e275721928f1e8b61157233ed958b8a1cf6e54a73895c8c50a",
        )
        self.assertEqual(
            VERIFY.DEFAULT_LIGAND_CONTRACT["sha256"],
            "9071449d7d4af5ca2b750b2fb3d58872c7dec532e61a833479a9f616aff170be",
        )
        self.assertEqual(
            VERIFY.EXPECTED_ATTEMPT_ORACLE["config_sha256"],
            "f2dbcf5687b9dcafc7e8a77a5351e72e14c363e444aed2216e8a6b777c8953c6",
        )
        self.assertEqual(
            VERIFY.EXPECTED_ATTEMPT_ORACLE["output_sha256"],
            "7638dc55934f89a136f2560166a30c62b4910bcaf1c11c3910024919f5783d9e",
        )
        self.assertEqual(
            VERIFY.EXPECTED_ATTEMPT_ORACLE["best_affinity_kcal_mol"],
            -0.6914,
        )

    def test_500_item_probe_requires_explicit_confirmation(self) -> None:
        result = VERIFY.verify_screening_batch_external(
            vina_path=ROOT / ".missing-should-not-be-read.exe",
            ligand_count=500,
            cancel_after=100,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "SCREENING_BENCHMARK_CONFIRMATION_REQUIRED",
        )
        self.assertEqual(
            result["error"]["details"]["external_processes_started"],
            0,
        )
        self.assertIsNone(
            VERIFY._large_run_guard(500, confirm_large_run=True)
        )

    def test_missing_vina_is_explicit_failure_and_cleans_workspace(self) -> None:
        missing = ROOT / ".missing-screening-benchmark-vina.exe"
        self.assertFalse(missing.exists())
        result = VERIFY.verify_screening_batch_external(
            vina_path=missing,
            ligand_count=3,
            cancel_after=2,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "SCREENING_BENCHMARK_FILE_MISSING",
        )
        self.assertEqual(result["process_audit"]["event_count"], 0)
        self.assertTrue(result["environment"]["work_directory_cleaned"])
        self.assertTrue(
            all(result["environment"]["variables_restored"].values())
        )

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = VERIFY.main(
                [
                    "--vina",
                    str(missing),
                    "--ligand-count",
                    "3",
                    "--cancel-after",
                    "2",
                ]
            )
        self.assertEqual(exit_code, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "SCREENING_BENCHMARK_FILE_MISSING",
        )

    def test_real_three_item_cancel_resume_probe_is_unconditional(self) -> None:
        result = VERIFY.verify_screening_batch_external(
            ligand_count=3,
            cancel_after=2,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            result["acceptance"]["profile"],
            "custom_screening_probe",
        )
        self.assertFalse(
            result["acceptance"]["formal_100_item_acceptance"]
        )
        self.assertEqual(
            result["cancellation_and_recovery"]["final_status"],
            "completed",
        )
        self.assertTrue(
            result["cancellation_and_recovery"]["request"][
                "process_alive_before_request"
            ]
        )
        self.assertEqual(result["results"]["succeeded_count"], 3)
        self.assertEqual(result["process_audit"]["event_count"], 3)
        self.assertTrue(result["process_audit"]["all_pids_positive"])
        self.assertTrue(result["process_audit"]["all_exit_codes_zero"])
        self.assertGreater(
            result["performance"]["benchmark_process_tree_memory"][
                "peak_tree_working_set_bytes"
            ],
            0,
        )
        self.assertTrue(result["environment"]["work_directory_cleaned"])

    def test_public_queue_handles_50_items_cancel_and_resume(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="DockStart_screening_50_contract_"
        ) as temporary:
            project = Path(temporary)
            prepared = project / "prepared"
            prepared.mkdir()
            receptor_source = VERIFY.DEFAULT_RECEPTOR_PATH
            ligand_source = VERIFY.DEFAULT_LIGAND_PATH
            shutil.copyfile(
                receptor_source,
                prepared / "receptor.pdbqt",
            )
            ligands: list[str] = []
            for index in range(1, 51):
                destination = prepared / f"ligand_{index:04d}.pdbqt"
                shutil.copyfile(ligand_source, destination)
                ligands.append(destination.relative_to(project).as_posix())
            fake_vina = project / "vina.exe"
            fake_vina.write_bytes(b"fixed fake vina")
            detection = SimpleNamespace(
                status="ok",
                path=str(fake_vina),
                version="1.2.7",
                source="configured",
                message="ok",
                raw_error="",
                capabilities={},
            )
            with patch(
                "dockstart_core.screening.vina_adapter.detect",
                return_value=detection,
            ):
                created = create_screening(
                    str(project),
                    "prepared/receptor.pdbqt",
                    ligands,
                    vina_path=str(fake_vina),
                    box=dict(VERIFY.BOX),
                    vina=dict(VERIFY.VINA_PARAMETERS),
                    max_retries=0,
                    top_n=20,
                )
            self.assertTrue(created["ok"], created)
            self.assertEqual(len(created["screening"]["queue"]), 50)

            calls: list[str] = []
            cancellation: dict[str, object] = {}

            def fake_runner(**kwargs):
                item_id = str(kwargs["item"]["item_id"])
                calls.append(item_id)
                kwargs["on_started"](os.getpid())
                Path(kwargs["stdout_path"]).write_text(
                    FAKE_LOG,
                    encoding="utf-8",
                )
                Path(kwargs["stderr_path"]).write_text("", encoding="utf-8")
                Path(kwargs["log_path"]).write_text(
                    FAKE_LOG,
                    encoding="utf-8",
                )
                Path(kwargs["output_path"]).write_text(
                    FAKE_OUTPUT,
                    encoding="utf-8",
                )
                if item_id == "ligand_0020" and not cancellation:
                    cancellation.update(
                        request_screening_cancel(str(project))
                    )
                return {
                    "pid": os.getpid(),
                    "exit_code": 0,
                    "error": "",
                }

            first = run_screening(str(project), runner=fake_runner)
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["screening"]["status"], "canceled")
            self.assertEqual(len(calls), 20)
            self.assertTrue(cancellation.get("ok"))
            self.assertEqual(
                first["screening"]["queue"],
                [f"ligand_{index:04d}" for index in range(21, 51)],
            )

            resumed = resume_screening(str(project))
            self.assertTrue(resumed["ok"], resumed)
            self.assertEqual(resumed["screening"]["status"], "ready")
            self.assertEqual(
                resumed["screening"]["queue"],
                [f"ligand_{index:04d}" for index in range(21, 51)],
            )
            finished = run_screening(str(project), runner=fake_runner)
            self.assertTrue(finished["ok"], finished)
            self.assertEqual(finished["screening"]["status"], "completed")
            self.assertEqual(
                calls,
                [f"ligand_{index:04d}" for index in range(1, 51)],
            )
            self.assertTrue(
                all(
                    item["status"] == "succeeded"
                    and item["attempt_count"] == 1
                    for item in finished["screening"]["items"]
                )
            )
            self.assertEqual(finished["screening"]["queue"], [])


if __name__ == "__main__":
    unittest.main()
