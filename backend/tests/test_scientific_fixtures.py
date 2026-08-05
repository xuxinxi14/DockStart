from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any, Iterator, Mapping


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "scientific"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_paths() -> list[Path]:
    return sorted(
        [
            *FIXTURE_ROOT.glob("*/fixture_manifest.json"),
            *FIXTURE_ROOT.glob("*/source_manifest.json"),
        ],
        key=lambda value: value.as_posix(),
    )


def _repository_records(value: Any) -> Iterator[Mapping[str, Any]]:
    """Yield only source-manifest records explicitly distributed in this repo."""

    if isinstance(value, Mapping):
        if isinstance(value.get("repository_path"), str):
            yield value
        for child in value.values():
            yield from _repository_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _repository_records(child)


class ScientificFixtureIntegrityTests(unittest.TestCase):
    def test_manifest_declared_files_have_expected_hashes(self) -> None:
        manifests = _manifest_paths()
        self.assertTrue(manifests)
        for manifest_path in manifests:
            with self.subTest(manifest=manifest_path.parent.name):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertIsInstance(manifest, dict)
                self.assertIn(manifest.get("schema_version"), {1, 2})
                self.assertTrue(str(manifest.get("fixture_id") or ""))

                declared_files = manifest.get("files")
                if not isinstance(declared_files, Mapping):
                    declared_files = {}
                for name, record in declared_files.items():
                    expected = str(record.get("sha256") or "")
                    if not expected:
                        continue
                    path = manifest_path.parent / name
                    self.assertTrue(path.is_file(), path)
                    self.assertEqual(_sha256(path), expected)
                    self.assertEqual(path.stat().st_size, record["size_bytes"])

                for record in _repository_records(manifest):
                    relative = Path(str(record["repository_path"]))
                    self.assertFalse(relative.is_absolute(), relative)
                    path = (REPOSITORY_ROOT / relative).resolve(strict=True)
                    path.relative_to(REPOSITORY_ROOT.resolve(strict=True))
                    self.assertTrue(path.is_file(), path)
                    self.assertEqual(_sha256(path), str(record["sha256"]))
                    self.assertEqual(path.stat().st_size, int(record["size_bytes"]))

    def test_metadata_only_manifests_require_only_explicit_repository_files(self) -> None:
        flexible_manifest = json.loads(
            (
                FIXTURE_ROOT / "flexible_ad4_1fpu" / "source_manifest.json"
            ).read_text(encoding="utf-8")
        )
        serial_manifest = json.loads(
            (
                FIXTURE_ROOT / "serial_screening_ad4" / "source_manifest.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(flexible_manifest["distribution"], "metadata_only")
        self.assertEqual(
            [record["repository_path"] for record in _repository_records(flexible_manifest)],
            [
                "backend/tests/fixtures/scientific/flexible_1fpu/1fpu_receptorH.pdb",
                "backend/tests/fixtures/scientific/flexible_1fpu/expected_bad_residues.json",
            ],
        )
        self.assertTrue(
            flexible_manifest["source_files"]["ligand_pdbqt"]["caller_supplied"]
        )
        self.assertEqual(serial_manifest["distribution"], "metadata_only")
        self.assertEqual(list(_repository_records(serial_manifest)), [])

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
