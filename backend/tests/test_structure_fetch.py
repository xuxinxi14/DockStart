from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.project import _project_from_dict, create_project, load_project, save_project  # noqa: E402
from dockstart_core.structure_fetch import (  # noqa: E402
    fetch_pdb_structure,
    fetch_pubchem_ligand,
    get_raw_files_status,
    validate_pdb_id,
    validate_pubchem_cid,
)


class StructureFetchTests(unittest.TestCase):
    def _create_project(self, temp_dir: str) -> Path:
        response = create_project("demo_project", temp_dir)
        self.assertTrue(response["ok"], response)
        return Path(response["project_dir"])

    def _fetcher(self, payload: bytes):
        def fetch(url: str, timeout: int) -> bytes:
            self.assertTrue(url.startswith("https://"))
            self.assertGreater(timeout, 0)
            return payload

        return fetch

    def test_validate_pdb_id_accepts_four_character_id(self) -> None:
        result = validate_pdb_id("1HSG")

        self.assertTrue(result["ok"])
        self.assertEqual(result["pdb_id"], "1HSG")

    def test_validate_pdb_id_accepts_lowercase_and_uppercases(self) -> None:
        result = validate_pdb_id("1hsg")

        self.assertTrue(result["ok"])
        self.assertEqual(result["pdb_id"], "1HSG")

    def test_validate_pdb_id_rejects_empty(self) -> None:
        result = validate_pdb_id("")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PDB_ID_REQUIRED")

    def test_validate_pdb_id_rejects_invalid_characters(self) -> None:
        result = validate_pdb_id("1H$G")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PDB_ID_INVALID")

    def test_validate_pubchem_cid_accepts_positive_integer(self) -> None:
        result = validate_pubchem_cid("2244")

        self.assertTrue(result["ok"])
        self.assertEqual(result["cid"], "2244")

    def test_validate_pubchem_cid_rejects_empty(self) -> None:
        result = validate_pubchem_cid("")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PUBCHEM_CID_REQUIRED")

    def test_validate_pubchem_cid_rejects_negative_number(self) -> None:
        result = validate_pubchem_cid("-1")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PUBCHEM_CID_INVALID")

    def test_validate_pubchem_cid_rejects_non_numeric(self) -> None:
        result = validate_pubchem_cid("aspirin")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PUBCHEM_CID_INVALID")

    def test_fetch_pdb_structure_writes_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pdb_structure(str(project_dir), "1hsg", fetcher=self._fetcher(b"HEADER TEST\n"))

            target = project_dir / "raw" / "receptor_1HSG.pdb"
            self.assertTrue(result["ok"])
            self.assertEqual(result["raw_file"], "raw/receptor_1HSG.pdb")
            self.assertEqual(target.read_bytes(), b"HEADER TEST\n")

    def test_fetch_pdb_structure_failure_returns_structured_error(self) -> None:
        def failing_fetcher(_url: str, _timeout: int) -> bytes:
            raise urllib.error.URLError("network down")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pdb_structure(str(project_dir), "1HSG", fetcher=failing_fetcher)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STRUCTURE_DOWNLOAD_NETWORK_ERROR")

    def test_existing_pdb_raw_file_without_overwrite_does_not_replace_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            target = project_dir / "raw" / "receptor_1HSG.pdb"
            target.write_bytes(b"old")

            result = fetch_pdb_structure(str(project_dir), "1HSG", overwrite=False, fetcher=self._fetcher(b"new"))

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "RAW_FILE_EXISTS")
            self.assertEqual(target.read_bytes(), b"old")

    def test_overwrite_true_replaces_pdb_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            target = project_dir / "raw" / "receptor_1HSG.pdb"
            target.write_bytes(b"old")

            result = fetch_pdb_structure(str(project_dir), "1HSG", overwrite=True, fetcher=self._fetcher(b"new"))

            self.assertTrue(result["ok"])
            self.assertEqual(target.read_bytes(), b"new")

    def test_fetch_pubchem_ligand_writes_sdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pubchem_ligand(str(project_dir), "2244", fetcher=self._fetcher(b"aspirin sdf\n"))

            target = project_dir / "raw" / "ligand_2244.sdf"
            self.assertTrue(result["ok"])
            self.assertEqual(result["raw_file"], "raw/ligand_2244.sdf")
            self.assertEqual(target.read_bytes(), b"aspirin sdf\n")

    def test_pubchem_download_failure_returns_structured_error(self) -> None:
        def failing_fetcher(_url: str, _timeout: int) -> bytes:
            raise RuntimeError("fake failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pubchem_ligand(str(project_dir), "2244", fetcher=failing_fetcher)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STRUCTURE_DOWNLOAD_ERROR")

    def test_project_json_records_receptor_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pdb_structure(str(project_dir), "1HSG", fetcher=self._fetcher(b"HEADER\n"))
            loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(loaded["receptor"]["source"], "rcsb_pdb")
        self.assertEqual(loaded["receptor"]["source_id"], "1HSG")
        self.assertEqual(loaded["receptor"]["raw_file"], "raw/receptor_1HSG.pdb")

    def test_project_json_records_ligand_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pubchem_ligand(str(project_dir), 2244, fetcher=self._fetcher(b"sdf\n"))
            loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(loaded["ligand"]["source"], "pubchem")
        self.assertEqual(loaded["ligand"]["source_id"], "2244")
        self.assertEqual(loaded["ligand"]["raw_file"], "raw/ligand_2244.sdf")

    def test_fetch_does_not_replace_existing_prepared_receptor_file_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            loaded = load_project(str(project_dir))
            project = loaded["project"]
            project["receptor"]["file"] = "prepared/custom_receptor.pdbqt"
            save_project_response = save_project(_project_from_dict(project, project_dir))
            self.assertTrue(save_project_response["ok"], save_project_response)

            result = fetch_pdb_structure(str(project_dir), "1HSG", fetcher=self._fetcher(b"HEADER\n"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["project"]["receptor"]["file"], "prepared/custom_receptor.pdbqt")
        self.assertEqual(result["project"]["receptor"]["raw_file"], "raw/receptor_1HSG.pdb")

    def test_fetch_does_not_replace_existing_prepared_ligand_file_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            loaded = load_project(str(project_dir))
            project = loaded["project"]
            project["ligand"]["file"] = "prepared/custom_ligand.pdbqt"
            save_project_response = save_project(_project_from_dict(project, project_dir))
            self.assertTrue(save_project_response["ok"], save_project_response)

            result = fetch_pubchem_ligand(str(project_dir), "2244", fetcher=self._fetcher(b"sdf\n"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["project"]["ligand"]["file"], "prepared/custom_ligand.pdbqt")
        self.assertEqual(result["project"]["ligand"]["raw_file"], "raw/ligand_2244.sdf")

    def test_get_raw_files_status_reports_downloaded_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            fetch_pdb_structure(str(project_dir), "1HSG", fetcher=self._fetcher(b"HEADER\n"))
            fetch_pubchem_ligand(str(project_dir), "2244", fetcher=self._fetcher(b"sdf\n"))

            result = get_raw_files_status(str(project_dir))

        statuses = {item["key"]: item for item in result["files"]}
        self.assertTrue(result["ok"])
        self.assertEqual(statuses["receptor_raw"]["status"], "ok")
        self.assertEqual(statuses["ligand_raw"]["status"], "ok")

    def test_structure_fetch_does_not_import_processing_or_docking_adapters(self) -> None:
        import dockstart_core.structure_fetch as structure_fetch

        self.assertFalse(hasattr(structure_fetch, "rdkit_adapter"))
        self.assertFalse(hasattr(structure_fetch, "meeko_adapter"))
        self.assertFalse(hasattr(structure_fetch, "vina_adapter"))


if __name__ == "__main__":
    unittest.main()
