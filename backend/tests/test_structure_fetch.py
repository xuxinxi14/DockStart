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
    clear_ligand_raw_record,
    clear_receptor_raw_record,
    fetch_pdb_structure,
    fetch_pubchem_ligand,
    get_raw_files_status,
    import_ligand_raw_file,
    import_receptor_raw_file,
    validate_pdb_id,
    validate_pubchem_cid,
    validate_pubchem_name,
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

    def test_validate_pubchem_name_accepts_non_empty_name(self) -> None:
        result = validate_pubchem_name(" aspirin ")

        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "aspirin")

    def test_fetch_pdb_structure_writes_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pdb_structure(str(project_dir), "1hsg", fetcher=self._fetcher(b"HEADER TEST\n"))

            target = project_dir / "raw" / "receptor_1HSG.pdb"
            self.assertTrue(result["ok"])
            self.assertEqual(result["raw_file"], "raw/receptor_1HSG.pdb")
            self.assertEqual(target.read_bytes(), b"HEADER TEST\n")

    def test_fetch_pdb_structure_cif_writes_cif_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pdb_structure(str(project_dir), "1hsg", format="cif", fetcher=self._fetcher(b"data_1HSG\n"))

            target = project_dir / "raw" / "receptor_1HSG.cif"
            self.assertTrue(result["ok"])
            self.assertEqual(result["format"], "cif")
            self.assertEqual(result["raw_file"], "raw/receptor_1HSG.cif")
            self.assertEqual(target.read_bytes(), b"data_1HSG\n")

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

    def test_fetch_pubchem_ligand_by_name_writes_sdf(self) -> None:
        seen_urls: list[str] = []

        def fetcher(url: str, timeout: int) -> bytes:
            self.assertGreater(timeout, 0)
            seen_urls.append(url)
            return b"aspirin sdf by name\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pubchem_ligand(
                str(project_dir),
                "aspirin",
                query_type="name",
                fetcher=fetcher,
            )
            loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

            target = project_dir / "raw" / "ligand_name_aspirin.sdf"
            self.assertTrue(result["ok"], result)
            self.assertIn("/compound/name/aspirin/SDF", seen_urls[0])
            self.assertEqual(result["query_type"], "name")
            self.assertEqual(result["raw_file"], "raw/ligand_name_aspirin.sdf")
            self.assertEqual(target.read_bytes(), b"aspirin sdf by name\n")
            self.assertEqual(loaded["ligand"]["source"], "pubchem")
            self.assertEqual(loaded["ligand"]["source_id"], "aspirin")
            self.assertEqual(loaded["ligand"]["query_type"], "name")

    def test_fetch_pubchem_smiles_returns_unsupported_without_fetching(self) -> None:
        def fetcher(_url: str, _timeout: int) -> bytes:
            self.fail("SMILES placeholder must not call the network fetcher")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pubchem_ligand(str(project_dir), "CCO", query_type="smiles", fetcher=fetcher)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PUBCHEM_SMILES_UNSUPPORTED")

    def test_import_receptor_raw_file_copies_local_pdb(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "my receptor.pdb"
            source.write_text("HEADER LOCAL\n", encoding="utf-8")

            result = import_receptor_raw_file(str(project_dir), str(source))
            loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["source"], "local_file")
            self.assertEqual(result["raw_file"], "raw/receptor_my_receptor.pdb")
            self.assertEqual(loaded["receptor"]["source"], "local_file")
            self.assertEqual(loaded["receptor"]["source_id"], "my receptor.pdb")
            self.assertEqual(loaded["receptor"]["query_type"], "local_file")
            self.assertEqual(loaded["receptor"]["raw_file"], "raw/receptor_my_receptor.pdb")
            self.assertTrue((project_dir / "raw" / "receptor_my_receptor.pdb").is_file())

    def test_import_ligand_raw_file_copies_local_sdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "aspirin.sdf"
            source.write_text("ligand sdf\n", encoding="utf-8")

            result = import_ligand_raw_file(str(project_dir), str(source))
            loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["raw_file"], "raw/ligand_aspirin.sdf")
            self.assertEqual(loaded["ligand"]["source"], "local_file")
            self.assertEqual(loaded["ligand"]["source_id"], "aspirin.sdf")
            self.assertEqual(loaded["ligand"]["raw_file"], "raw/ligand_aspirin.sdf")
            self.assertTrue((project_dir / "raw" / "ligand_aspirin.sdf").is_file())

    def test_import_ligand_raw_file_rejects_unsupported_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "ligand.txt"
            source.write_text("not supported\n", encoding="utf-8")

            result = import_ligand_raw_file(str(project_dir), str(source))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LOCAL_RAW_FORMAT_UNSUPPORTED")

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
        self.assertEqual(loaded["receptor"]["query_type"], "pdb_id")
        self.assertEqual(loaded["receptor"]["raw_file"], "raw/receptor_1HSG.pdb")
        self.assertTrue(loaded["receptor"]["downloaded_at"])

    def test_project_json_records_ligand_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pubchem_ligand(str(project_dir), 2244, fetcher=self._fetcher(b"sdf\n"))
            loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(loaded["ligand"]["source"], "pubchem")
        self.assertEqual(loaded["ligand"]["source_id"], "2244")
        self.assertEqual(loaded["ligand"]["query_type"], "cid")
        self.assertEqual(loaded["ligand"]["raw_file"], "raw/ligand_2244.sdf")
        self.assertTrue(loaded["ligand"]["downloaded_at"])

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
        self.assertTrue(result["receptor"]["exists"])
        self.assertEqual(result["receptor"]["size_bytes"], len(b"HEADER\n"))
        self.assertTrue(result["receptor"]["modified_at"])
        self.assertTrue(result["receptor"]["absolute_path"])
        self.assertTrue(result["receptor"]["record_consistent"])

    def test_get_raw_files_status_without_raw_file_returns_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = get_raw_files_status(str(project_dir))

        self.assertTrue(result["ok"])
        self.assertFalse(result["receptor"]["exists"])
        self.assertEqual(result["receptor"]["size_bytes"], 0)
        self.assertFalse(result["receptor"]["record_consistent"])

    def test_get_raw_files_status_detects_missing_recorded_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            loaded = load_project(str(project_dir))
            project = loaded["project"]
            project["receptor"]["source"] = "rcsb_pdb"
            project["receptor"]["source_id"] = "1HSG"
            project["receptor"]["raw_file"] = "raw/receptor_1HSG.pdb"
            self.assertTrue(save_project(_project_from_dict(project, project_dir))["ok"])

            result = get_raw_files_status(str(project_dir))

        self.assertTrue(result["ok"])
        self.assertFalse(result["receptor"]["exists"])
        self.assertFalse(result["receptor"]["record_consistent"])
        self.assertEqual(result["receptor"]["status"], "missing")

    def test_clear_receptor_raw_record_preserves_prepared_file_reference_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            fetch_pdb_structure(str(project_dir), "1HSG", fetcher=self._fetcher(b"HEADER\n"))
            prepared = project_dir / "prepared" / "receptor.pdbqt"
            prepared.write_text("prepared receptor\n", encoding="utf-8")
            raw = project_dir / "raw" / "receptor_1HSG.pdb"

            result = clear_receptor_raw_record(str(project_dir))
            loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(loaded["receptor"]["file"], "prepared/receptor.pdbqt")
            self.assertEqual(loaded["receptor"]["source"], "")
            self.assertEqual(loaded["receptor"]["source_id"], "")
            self.assertEqual(loaded["receptor"]["query_type"], "")
            self.assertEqual(loaded["receptor"]["downloaded_at"], "")
            self.assertEqual(loaded["receptor"]["raw_file"], "")
            self.assertTrue(raw.exists())
            self.assertTrue(prepared.exists())

    def test_clear_ligand_raw_record_preserves_prepared_file_reference_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            fetch_pubchem_ligand(str(project_dir), "2244", fetcher=self._fetcher(b"sdf\n"))
            prepared = project_dir / "prepared" / "ligand.pdbqt"
            prepared.write_text("prepared ligand\n", encoding="utf-8")
            raw = project_dir / "raw" / "ligand_2244.sdf"

            result = clear_ligand_raw_record(str(project_dir))
            loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(loaded["ligand"]["file"], "prepared/ligand.pdbqt")
            self.assertEqual(loaded["ligand"]["source"], "")
            self.assertEqual(loaded["ligand"]["source_id"], "")
            self.assertEqual(loaded["ligand"]["query_type"], "")
            self.assertEqual(loaded["ligand"]["downloaded_at"], "")
            self.assertEqual(loaded["ligand"]["raw_file"], "")
            self.assertTrue(raw.exists())
            self.assertTrue(prepared.exists())

    def test_clear_raw_record_with_delete_file_only_deletes_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            fetch_pdb_structure(str(project_dir), "1HSG", fetcher=self._fetcher(b"HEADER\n"))
            raw = project_dir / "raw" / "receptor_1HSG.pdb"
            prepared = project_dir / "prepared" / "receptor.pdbqt"
            prepared.write_text("prepared receptor\n", encoding="utf-8")

            result = clear_receptor_raw_record(str(project_dir), delete_file=True)

            self.assertTrue(result["ok"], result)
            self.assertFalse(raw.exists())
            self.assertTrue(prepared.exists())
            self.assertTrue(result["deleted_file"])

    def test_clear_raw_record_refuses_delete_outside_raw_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            prepared = project_dir / "prepared" / "receptor.pdbqt"
            prepared.write_text("prepared receptor\n", encoding="utf-8")
            loaded = load_project(str(project_dir))
            project = loaded["project"]
            project["receptor"]["source"] = "rcsb_pdb"
            project["receptor"]["source_id"] = "1HSG"
            project["receptor"]["raw_file"] = "../demo_project/prepared/receptor.pdbqt"
            self.assertTrue(save_project(_project_from_dict(project, project_dir))["ok"])

            result = clear_receptor_raw_record(str(project_dir), delete_file=True)

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "RAW_DELETE_OUTSIDE_RAW_DIR")
            self.assertTrue(prepared.exists())

    def test_structure_fetch_does_not_import_processing_or_docking_adapters(self) -> None:
        import dockstart_core.structure_fetch as structure_fetch

        self.assertFalse(hasattr(structure_fetch, "rdkit_adapter"))
        self.assertFalse(hasattr(structure_fetch, "meeko_adapter"))
        self.assertFalse(hasattr(structure_fetch, "vina_adapter"))


if __name__ == "__main__":
    unittest.main()
