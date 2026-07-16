from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.candidate_preview import MAX_PREVIEW_BYTES, main, preview_candidate_structure  # noqa: E402


class CandidatePreviewTests(unittest.TestCase):
    def test_requires_explicit_candidate_selection(self) -> None:
        result = preview_candidate_structure(None)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STRUCTURE_PREVIEW_SELECTION_REQUIRED")
        self.assertEqual(result["content"], "")

    def test_previews_selected_rcsb_pdb_in_memory(self) -> None:
        seen_urls: list[str] = []
        payload = b"HEADER TEST\nATOM      1  N   GLY A   1       0.000   0.000   0.000\nEND\n"

        def fetcher(url: str, timeout: int) -> bytes:
            seen_urls.append(url)
            self.assertGreater(timeout, 0)
            return payload

        result = preview_candidate_structure(
            {"download_command": "fetch-pdb", "pdb_id": "1iep", "format": "pdb"},
            fetcher=fetcher,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provider"], "rcsb")
        self.assertEqual(result["source_id"], "1IEP")
        self.assertEqual(result["format"], "pdb")
        self.assertEqual(result["content"], payload.decode("utf-8"))
        self.assertEqual(result["size_bytes"], len(payload))
        self.assertEqual(seen_urls, ["https://files.rcsb.org/download/1IEP.pdb"])
        self.assertNotIn("project", result)

    def test_previews_selected_pubchem_name_with_encoded_url(self) -> None:
        seen_urls: list[str] = []

        def fetcher(url: str, _timeout: int) -> bytes:
            seen_urls.append(url)
            return b"aspirin\n  DockStart\n\nM  END\n$$$$\n"

        result = preview_candidate_structure(
            {
                "download_command": "fetch-pubchem",
                "query": "acetyl salicylic acid",
                "query_type": "name",
                "format": "sdf",
            },
            fetcher=fetcher,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provider"], "pubchem")
        self.assertEqual(result["format"], "sdf")
        self.assertIn("acetyl%20salicylic%20acid/SDF", seen_urls[0])

    def test_rejects_payload_over_hard_preview_limit(self) -> None:
        result = preview_candidate_structure(
            {"download_command": "fetch-pdb", "pdb_id": "1IEP", "format": "pdb"},
            fetcher=lambda _url, _timeout: b"12345",
            byte_limit=4,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STRUCTURE_PREVIEW_TOO_LARGE")
        self.assertEqual(result["content"], "")

    def test_complete_json_response_stays_under_hard_limit(self) -> None:
        # Backslashes are escaped in JSON, so the response can exceed the raw
        # byte count even when the download itself is within the limit.
        payload = b"\\" * (MAX_PREVIEW_BYTES // 2 + 1)
        result = preview_candidate_structure(
            {"download_command": "fetch-pdb", "pdb_id": "1IEP", "format": "pdb"},
            fetcher=lambda _url, _timeout: payload,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STRUCTURE_PREVIEW_TOO_LARGE")
        self.assertEqual(result["content"], "")

    def test_custom_interactive_limit_applies_to_serialized_response(self) -> None:
        byte_limit = 2_048
        payload = b"\\" * 1_200
        result = preview_candidate_structure(
            {"download_command": "fetch-pdb", "pdb_id": "1IEP", "format": "pdb"},
            fetcher=lambda _url, _timeout: payload,
            byte_limit=byte_limit,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STRUCTURE_PREVIEW_TOO_LARGE")
        self.assertIn(f"limit_bytes={byte_limit}", result["error"]["raw_error"])

    def test_fetcher_has_total_wall_clock_deadline(self) -> None:
        release = threading.Event()

        def slow_fetcher(_url: str, _timeout: int) -> bytes:
            release.wait(timeout=2)
            return b"HEADER TEST\nEND\n"

        started = time.monotonic()
        result = preview_candidate_structure(
            {"download_command": "fetch-pdb", "pdb_id": "1IEP", "format": "pdb"},
            fetcher=slow_fetcher,
            timeout=0.05,
        )
        elapsed = time.monotonic() - started
        release.set()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STRUCTURE_PREVIEW_TIMEOUT")
        self.assertLess(elapsed, 0.5)

    def test_network_failure_is_chinese_structured_error(self) -> None:
        def fetcher(_url: str, _timeout: int) -> bytes:
            raise urllib.error.URLError("offline")

        result = preview_candidate_structure(
            {
                "download_command": "fetch-pubchem",
                "query": "2244",
                "query_type": "cid",
                "format": "sdf",
            },
            fetcher=fetcher,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STRUCTURE_PREVIEW_NETWORK_ERROR")
        self.assertIn("网络", result["error"]["message"])
        self.assertEqual(result["provider"], "pubchem")

    def test_cli_reports_missing_selection_as_json_without_network(self) -> None:
        output = io.StringIO()
        with patch.object(sys, "argv", ["candidate_preview", "preview-candidate"]):
            with contextlib.redirect_stdout(output):
                main()

        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "STRUCTURE_PREVIEW_SELECTION_REQUIRED")

    def test_cli_accepts_interactive_preview_limit(self) -> None:
        output = io.StringIO()
        selection = json.dumps(
            {"download_command": "fetch-pdb", "pdb_id": "1IEP", "format": "pdb"},
        )
        with patch.object(sys, "argv", ["candidate_preview", "preview-candidate", selection, "0"]):
            with contextlib.redirect_stdout(output):
                main()

        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "STRUCTURE_PREVIEW_LIMIT_INVALID")

    def test_cli_forwards_valid_interactive_preview_limit(self) -> None:
        output = io.StringIO()
        selection = json.dumps(
            {"download_command": "fetch-pdb", "pdb_id": "1IEP", "format": "pdb"},
        )
        with patch.object(
            sys,
            "argv",
            ["candidate_preview", "preview-candidate", selection, str(2 * 1024 * 1024)],
        ):
            with patch(
                "dockstart_core.candidate_preview.preview_candidate_structure",
                return_value={"ok": True},
            ) as preview:
                with contextlib.redirect_stdout(output):
                    main()

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(preview.call_args.kwargs["byte_limit"], 2 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
