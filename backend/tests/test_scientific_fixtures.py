from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "scientific"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ScientificFixtureIntegrityTests(unittest.TestCase):
    def test_manifest_declared_files_have_expected_hashes(self) -> None:
        for manifest_path in FIXTURE_ROOT.glob("*/fixture_manifest.json"):
            with self.subTest(manifest=manifest_path.parent.name):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for name, record in manifest["files"].items():
                    expected = str(record.get("sha256") or "")
                    if not expected:
                        continue
                    path = manifest_path.parent / name
                    self.assertTrue(path.is_file(), path)
                    self.assertEqual(_sha256(path), expected)
                    self.assertEqual(path.stat().st_size, record["size_bytes"])

    def test_1fpu_review_fixture_selects_existing_thr315(self) -> None:
        fixture = FIXTURE_ROOT / "flexible_1fpu"
        pdb_text = (fixture / "1fpu_receptorH.pdb").read_text(
            encoding="utf-8",
            errors="replace",
        )
        review = json.loads(
            (fixture / "expected_bad_residues.json").read_text(encoding="utf-8")
        )

        selected_lines = [
            line
            for line in pdb_text.splitlines()
            if line.startswith("ATOM")
            and line[21:22].strip() == "A"
            and line[22:26].strip() == "315"
        ]
        self.assertTrue(selected_lines)
        self.assertTrue(all(line[17:20].strip() == "THR" for line in selected_lines))
        self.assertNotIn("A:315", review["expected_bad_residues"])

    def test_bace1_sdf_retains_one_complete_record(self) -> None:
        sdf = (
            FIXTURE_ROOT
            / "macrocycle_bace1"
            / "BACE_1_ligand.sdf"
        ).read_text(encoding="utf-8", errors="replace")
        self.assertEqual(sdf.count("$$$$"), 1)
        self.assertIn("V2000", sdf)

    def test_hydrated_1uw6_is_metadata_only_and_portably_pinned(self) -> None:
        fixture = FIXTURE_ROOT / "hydrated_1uw6"
        manifest = json.loads(
            (fixture / "source_manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["distribution"], "metadata_only")
        self.assertFalse(manifest["contains_upstream_structure_files"])
        self.assertEqual(
            manifest["upstream"]["checkout_profile"][
                "portable_identity_algorithm"
            ],
            "normalize_crlf_and_cr_to_lf_then_sha256",
        )
        self.assertEqual(
            manifest["upstream"]["checkout_profile"][
                "accepted_worktree_line_endings"
            ],
            ["LF", "CRLF"],
        )

        required_files = manifest["required_files"]
        self.assertIn("official_hydrated_ligand", required_files)
        self.assertIn("map_W", required_files)
        for name, record in required_files.items():
            with self.subTest(name=name):
                identity = record["portable_text_identity"]
                self.assertEqual(
                    identity["algorithm"],
                    "normalize_crlf_and_cr_to_lf_then_sha256",
                )
                self.assertGreater(identity["size_bytes"], 0)
                self.assertEqual(len(identity["sha256"]), 64)
                self.assertEqual(len(record["sha256"]), 64)

        distributed_names = {
            path.name for path in fixture.iterdir() if path.is_file()
        }
        self.assertEqual(
            distributed_names,
            {"README.md", "source_manifest.json"},
        )


if __name__ == "__main__":
    unittest.main()
