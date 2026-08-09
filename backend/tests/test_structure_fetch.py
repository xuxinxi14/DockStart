from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.project import _project_from_dict, create_project, load_project, save_project  # noqa: E402
from dockstart_core import project as project_module  # noqa: E402
from dockstart_core import structure_fetch as structure_fetch_module  # noqa: E402
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

    def _set_preparation_status(self, project_dir: Path, target: str, status: str) -> None:
        project = load_project(str(project_dir))["project"]
        project["preparation"][target]["status"] = status
        project["preparation"][target]["prep_id"] = f"{target}_running_test"
        saved = save_project(_project_from_dict(project, project_dir))
        self.assertTrue(saved["ok"], saved)

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
            self.assertEqual(result["project"]["receptor"]["file"], "")
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

            result = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                overwrite=False,
                fetcher=self._fetcher(b"HEADER NEW\n"),
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "RAW_FILE_EXISTS")
            self.assertEqual(target.read_bytes(), b"old")

    def test_overwrite_true_replaces_pdb_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            target = project_dir / "raw" / "receptor_1HSG.pdb"
            target.write_bytes(b"old")

            result = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                overwrite=True,
                fetcher=self._fetcher(b"HEADER NEW\n"),
            )

            self.assertTrue(result["ok"])
            self.assertEqual(target.read_bytes(), b"HEADER NEW\n")

    def test_fetch_pubchem_ligand_writes_sdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            result = fetch_pubchem_ligand(
                str(project_dir),
                "2244",
                fetcher=self._fetcher(b"aspirin sdf\nM  END\n$$$$\n"),
            )

            target = project_dir / "raw" / "ligand_2244.sdf"
            self.assertTrue(result["ok"])
            self.assertEqual(result["raw_file"], "raw/ligand_2244.sdf")
            self.assertEqual(result["project"]["ligand"]["file"], "")
            self.assertEqual(target.read_bytes(), b"aspirin sdf\nM  END\n$$$$\n")

    def test_fetch_pubchem_ligand_by_name_writes_sdf(self) -> None:
        seen_urls: list[str] = []

        def fetcher(url: str, timeout: int) -> bytes:
            self.assertGreater(timeout, 0)
            seen_urls.append(url)
            return b"aspirin sdf by name\nM  END\n$$$$\n"

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
            self.assertEqual(target.read_bytes(), b"aspirin sdf by name\nM  END\n$$$$\n")
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
            self.assertEqual(loaded["receptor"]["file"], "")
            self.assertTrue((project_dir / "raw" / "receptor_my_receptor.pdb").is_file())

    def test_import_receptor_raw_file_accepts_local_cif(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "receptor.cif"
            source.write_text("data_receptor\n", encoding="utf-8")

            result = import_receptor_raw_file(str(project_dir), str(source))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["format"], "cif")

    def test_import_receptor_raw_file_rejects_pdbqt_with_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "receptor.pdbqt"
            source.write_text("ATOM\n", encoding="utf-8")

            result = import_receptor_raw_file(str(project_dir), str(source))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LOCAL_RAW_FORMAT_UNSUPPORTED")
        self.assertIn("已有 PDBQT", result["error"]["suggestion"])

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
            self.assertEqual(loaded["ligand"]["file"], "")
            self.assertTrue((project_dir / "raw" / "ligand_aspirin.sdf").is_file())

    def test_import_ligand_raw_file_accepts_local_mol(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "ligand.mol"
            source.write_text("mock mol\n", encoding="utf-8")

            result = import_ligand_raw_file(str(project_dir), str(source))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["format"], "mol")

    def test_import_ligand_raw_file_accepts_local_mol2(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "ligand.mol2"
            source.write_text("mock mol2\n", encoding="utf-8")

            result = import_ligand_raw_file(str(project_dir), str(source))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["format"], "mol2")

    def test_import_ligand_raw_file_rejects_unsupported_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "ligand.txt"
            source.write_text("not supported\n", encoding="utf-8")

            result = import_ligand_raw_file(str(project_dir), str(source))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LOCAL_RAW_FORMAT_UNSUPPORTED")

    def test_local_receptor_import_is_blocked_while_receptor_preparation_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "replacement_receptor.pdb"
            source.write_text("HEADER REPLACEMENT\n", encoding="utf-8")
            self._set_preparation_status(project_dir, "receptor", "running")
            project_before = (project_dir / "project.json").read_bytes()

            result = import_receptor_raw_file(str(project_dir), str(source))
            project_after = (project_dir / "project.json").read_bytes()
            target_exists = (project_dir / "raw" / "receptor_replacement_receptor.pdb").exists()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PREPARATION_IN_PROGRESS")
        self.assertIn("受体格式转换", result["error"]["message"])
        self.assertEqual(project_after, project_before)
        self.assertFalse(target_exists)

    def test_local_ligand_import_is_not_blocked_by_other_target_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "replacement_ligand.sdf"
            source.write_text("LIGAND\n", encoding="utf-8")
            self._set_preparation_status(project_dir, "receptor", "running")

            result = import_ligand_raw_file(str(project_dir), str(source))

        self.assertTrue(result["ok"], result)

    def test_interrupted_preparation_does_not_block_stale_recovery_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "recovery_receptor.pdb"
            source.write_text("HEADER RECOVERY\n", encoding="utf-8")
            self._set_preparation_status(project_dir, "receptor", "interrupted")

            result = import_receptor_raw_file(str(project_dir), str(source))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["raw_file"], "raw/receptor_recovery_receptor.pdb")

    def test_rcsb_replacement_is_blocked_before_network_while_receptor_preparation_runs(self) -> None:
        def unexpected_fetcher(_url: str, _timeout: int) -> bytes:
            self.fail("blocked RCSB replacement must not call the network")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw = project_dir / "raw" / "receptor_1HSG.pdb"
            raw.write_bytes(b"ORIGINAL RECEPTOR\n")
            self._set_preparation_status(project_dir, "receptor", "running")
            project_before = (project_dir / "project.json").read_bytes()

            result = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                overwrite=True,
                fetcher=unexpected_fetcher,
            )
            project_after = (project_dir / "project.json").read_bytes()
            raw_after = raw.read_bytes()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PREPARATION_IN_PROGRESS")
        self.assertEqual(raw_after, b"ORIGINAL RECEPTOR\n")
        self.assertEqual(project_after, project_before)

    def test_pubchem_replacement_is_blocked_before_network_while_ligand_preparation_runs(self) -> None:
        def unexpected_fetcher(_url: str, _timeout: int) -> bytes:
            self.fail("blocked PubChem replacement must not call the network")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw = project_dir / "raw" / "ligand_2244.sdf"
            raw.write_bytes(b"ORIGINAL LIGAND\n")
            self._set_preparation_status(project_dir, "ligand", "running")
            project_before = (project_dir / "project.json").read_bytes()

            result = fetch_pubchem_ligand(
                str(project_dir),
                "2244",
                overwrite=True,
                fetcher=unexpected_fetcher,
            )
            project_after = (project_dir / "project.json").read_bytes()
            raw_after = raw.read_bytes()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "PREPARATION_IN_PROGRESS")
        self.assertEqual(raw_after, b"ORIGINAL LIGAND\n")
        self.assertEqual(project_after, project_before)

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

            result = fetch_pubchem_ligand(
                str(project_dir),
                2244,
                fetcher=self._fetcher(b"ligand sdf\nM  END\n$$$$\n"),
            )
            loaded = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual(loaded["ligand"]["source"], "pubchem")
        self.assertEqual(loaded["ligand"]["source_id"], "2244")
        self.assertEqual(loaded["ligand"]["query_type"], "cid")
        self.assertEqual(loaded["ligand"]["raw_file"], "raw/ligand_2244.sdf")
        self.assertTrue(loaded["ligand"]["downloaded_at"])

    def test_download_clears_legacy_missing_prepared_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project = load_project(str(project_dir))["project"]
            project["receptor"]["file"] = "prepared/receptor.pdbqt"
            self.assertTrue(save_project(_project_from_dict(project, project_dir))["ok"])

            result = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                fetcher=self._fetcher(b"HEADER\n"),
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["project"]["receptor"]["file"], "")

    def test_local_import_invalidates_existing_prepared_reference_but_keeps_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project = load_project(str(project_dir))["project"]
            project["ligand"]["file"] = "prepared/ligand.pdbqt"
            prepared = project_dir / "prepared" / "ligand.pdbqt"
            prepared.write_text("prepared ligand\n", encoding="utf-8")
            self.assertTrue(save_project(_project_from_dict(project, project_dir))["ok"])
            source = Path(temp_dir) / "ligand.sdf"
            source.write_text("mock sdf\n", encoding="utf-8")

            result = import_ligand_raw_file(str(project_dir), str(source))
            prepared_exists = prepared.is_file()

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["project"]["ligand"]["file"], "")
        self.assertTrue(prepared_exists)

    def test_fetch_invalidates_existing_prepared_receptor_reference_but_keeps_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            loaded = load_project(str(project_dir))
            project = loaded["project"]
            project["receptor"]["file"] = "prepared/custom_receptor.pdbqt"
            (project_dir / "prepared" / "custom_receptor.pdbqt").write_text(
                "prepared receptor\n",
                encoding="utf-8",
            )
            save_project_response = save_project(_project_from_dict(project, project_dir))
            self.assertTrue(save_project_response["ok"], save_project_response)

            result = fetch_pdb_structure(str(project_dir), "1HSG", fetcher=self._fetcher(b"HEADER\n"))
            prepared_exists = (project_dir / "prepared" / "custom_receptor.pdbqt").is_file()

        self.assertTrue(result["ok"])
        self.assertEqual(result["project"]["receptor"]["file"], "")
        self.assertEqual(result["project"]["receptor"]["raw_file"], "raw/receptor_1HSG.pdb")
        self.assertTrue(prepared_exists)

    def test_fetch_invalidates_existing_prepared_ligand_reference_but_keeps_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            loaded = load_project(str(project_dir))
            project = loaded["project"]
            project["ligand"]["file"] = "prepared/custom_ligand.pdbqt"
            (project_dir / "prepared" / "custom_ligand.pdbqt").write_text(
                "prepared ligand\n",
                encoding="utf-8",
            )
            save_project_response = save_project(_project_from_dict(project, project_dir))
            self.assertTrue(save_project_response["ok"], save_project_response)

            result = fetch_pubchem_ligand(
                str(project_dir),
                "2244",
                fetcher=self._fetcher(b"ligand sdf\nM  END\n$$$$\n"),
            )
            prepared_exists = (project_dir / "prepared" / "custom_ligand.pdbqt").is_file()

        self.assertTrue(result["ok"])
        self.assertEqual(result["project"]["ligand"]["file"], "")
        self.assertEqual(result["project"]["ligand"]["raw_file"], "raw/ligand_2244.sdf")
        self.assertTrue(prepared_exists)

    def test_failed_raw_acquisition_preserves_prepared_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            target = project_dir / "raw" / "receptor_1HSG.pdb"
            target.write_bytes(b"existing raw\n")
            prepared = project_dir / "prepared" / "custom_receptor.pdbqt"
            prepared.write_text("prepared receptor\n", encoding="utf-8")
            project = load_project(str(project_dir))["project"]
            project["receptor"]["file"] = "prepared/custom_receptor.pdbqt"
            self.assertTrue(save_project(_project_from_dict(project, project_dir))["ok"])

            result = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                overwrite=False,
                fetcher=self._fetcher(b"HEADER REPLACEMENT\n"),
            )
            loaded = load_project(str(project_dir))["project"]
            target_content = target.read_bytes()
            prepared_exists = prepared.is_file()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "RAW_FILE_EXISTS")
        self.assertEqual(loaded["receptor"]["file"], "prepared/custom_receptor.pdbqt")
        self.assertEqual(target_content, b"existing raw\n")
        self.assertTrue(prepared_exists)

    def test_get_raw_files_status_reports_downloaded_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            fetch_pdb_structure(str(project_dir), "1HSG", fetcher=self._fetcher(b"HEADER\n"))
            fetch_pubchem_ligand(
                str(project_dir),
                "2244",
                fetcher=self._fetcher(b"ligand sdf\nM  END\n$$$$\n"),
            )

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
            loaded_project = load_project(str(project_dir))["project"]
            loaded_project["receptor"]["file"] = "prepared/receptor.pdbqt"
            self.assertTrue(save_project(_project_from_dict(loaded_project, project_dir))["ok"])
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
            fetch_pubchem_ligand(
                str(project_dir),
                "2244",
                fetcher=self._fetcher(b"ligand sdf\nM  END\n$$$$\n"),
            )
            prepared = project_dir / "prepared" / "ligand.pdbqt"
            prepared.write_text("prepared ligand\n", encoding="utf-8")
            loaded_project = load_project(str(project_dir))["project"]
            loaded_project["ligand"]["file"] = "prepared/ligand.pdbqt"
            self.assertTrue(save_project(_project_from_dict(loaded_project, project_dir))["ok"])
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

    def test_local_raw_import_rolls_back_new_file_when_project_save_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = Path(temp_dir) / "ligand.sdf"
            source.write_text("local ligand\n", encoding="utf-8")
            target = project_dir / "raw" / "ligand_ligand.sdf"
            project_before = (project_dir / "project.json").read_bytes()
            conflict = project_module._error(
                "PROJECT_SAVE_CONFLICT",
                "project.json 已被其他操作更新。",
            )

            with unittest.mock.patch.object(
                structure_fetch_module,
                "save_project",
                return_value=conflict,
            ):
                result = import_ligand_raw_file(str(project_dir), str(source))

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "PROJECT_SAVE_CONFLICT")
            self.assertFalse(target.exists())
            self.assertEqual((project_dir / "project.json").read_bytes(), project_before)

    def test_raw_overwrite_rolls_back_old_bytes_when_project_save_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            target = project_dir / "raw" / "receptor_1HSG.pdb"
            target.write_bytes(b"HEADER OLD\n")
            project_before = (project_dir / "project.json").read_bytes()
            conflict = project_module._error(
                "PROJECT_SAVE_CONFLICT",
                "project.json 已被其他操作更新。",
            )

            with unittest.mock.patch.object(
                structure_fetch_module,
                "save_project",
                return_value=conflict,
            ):
                result = fetch_pdb_structure(
                    str(project_dir),
                    "1HSG",
                    overwrite=True,
                    fetcher=self._fetcher(b"HEADER NEW\n"),
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "PROJECT_SAVE_CONFLICT")
            self.assertEqual(target.read_bytes(), b"HEADER OLD\n")
            self.assertEqual((project_dir / "project.json").read_bytes(), project_before)

    def test_clear_raw_delete_rolls_back_file_when_project_save_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            fetched = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                fetcher=self._fetcher(b"HEADER ORIGINAL\n"),
            )
            self.assertTrue(fetched["ok"], fetched)
            target = project_dir / "raw" / "receptor_1HSG.pdb"
            project_before = (project_dir / "project.json").read_bytes()
            conflict = project_module._error(
                "PROJECT_SAVE_CONFLICT",
                "project.json 已被其他操作更新。",
            )

            with unittest.mock.patch.object(
                structure_fetch_module,
                "save_project",
                return_value=conflict,
            ):
                result = clear_receptor_raw_record(str(project_dir), delete_file=True)

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "PROJECT_SAVE_CONFLICT")
            self.assertEqual(target.read_bytes(), b"HEADER ORIGINAL\n")
            self.assertEqual((project_dir / "project.json").read_bytes(), project_before)

    def test_raw_overwrite_rejects_hardlink_target_without_touching_external_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            external = Path(temp_dir) / "external_receptor.pdb"
            external.write_bytes(b"HEADER EXTERNAL\n")
            target = project_dir / "raw" / "receptor_1HSG.pdb"
            try:
                os.link(external, target)
            except OSError as exc:
                self.skipTest(f"当前文件系统不支持硬链接测试：{exc}")

            result = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                overwrite=True,
                fetcher=self._fetcher(b"HEADER NEW\n"),
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "PROJECT_FILE_TARGET_HARDLINKED")
            self.assertEqual(external.read_bytes(), b"HEADER EXTERNAL\n")
            self.assertEqual(target.read_bytes(), b"HEADER EXTERNAL\n")

    def test_raw_directory_symlink_is_rejected_without_writing_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_dir = project_dir / "raw"
            outside = Path(temp_dir) / "outside"
            outside.mkdir()
            raw_dir.rmdir()
            try:
                raw_dir.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                raw_dir.mkdir()
                self.skipTest(f"当前环境不允许创建目录符号链接：{exc}")

            result = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                fetcher=self._fetcher(b"HEADER NEW\n"),
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "PROJECT_STORAGE_PATH_UNSAFE")
            self.assertFalse((outside / "receptor_1HSG.pdb").exists())

    def test_overwrite_false_cannot_replace_concurrently_created_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_before = (project_dir / "project.json").read_bytes()
            original_create = project_module._atomic_create_bytes_no_replace

            def create_racer(path: Path, payload: bytes, parent_identity: tuple[int, int]) -> None:
                path.write_bytes(b"HEADER RACER\n")
                original_create(path, payload, parent_identity)

            with unittest.mock.patch.object(
                project_module,
                "_atomic_create_bytes_no_replace",
                side_effect=create_racer,
            ):
                result = fetch_pdb_structure(
                    str(project_dir),
                    "1HSG",
                    overwrite=False,
                    fetcher=self._fetcher(b"HEADER DOWNLOADED\n"),
                )

            target = project_dir / "raw" / "receptor_1HSG.pdb"
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "RAW_FILE_EXISTS")
            self.assertEqual(target.read_bytes(), b"HEADER RACER\n")
            self.assertEqual((project_dir / "project.json").read_bytes(), project_before)

    def test_download_rejects_html_error_page_before_raw_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_before = (project_dir / "project.json").read_bytes()

            result = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                fetcher=self._fetcher(b"<!doctype html><html>not found</html>"),
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "STRUCTURE_DOWNLOAD_FORMAT_INVALID")
            self.assertFalse((project_dir / "raw" / "receptor_1HSG.pdb").exists())
            self.assertEqual((project_dir / "project.json").read_bytes(), project_before)

    def test_download_enforces_actual_byte_limit_for_custom_fetcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            with unittest.mock.patch.object(
                structure_fetch_module,
                "MAX_STRUCTURE_DOWNLOAD_BYTES",
                16,
            ):
                result = fetch_pdb_structure(
                    str(project_dir),
                    "1HSG",
                    fetcher=self._fetcher(b"HEADER TOO LARGE\n"),
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "STRUCTURE_DOWNLOAD_TOO_LARGE")

    def test_download_enforces_wall_clock_deadline_for_custom_fetcher(self) -> None:
        def slow_fetcher(_url: str, _timeout: float) -> bytes:
            time.sleep(0.3)
            return b"HEADER LATE\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            started = time.monotonic()
            result = fetch_pdb_structure(
                str(project_dir),
                "1HSG",
                fetcher=slow_fetcher,
                timeout=0.02,
            )
            elapsed = time.monotonic() - started

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "STRUCTURE_DOWNLOAD_TIMEOUT")
            self.assertLess(elapsed, 0.2)

    def test_network_reader_rejects_oversized_content_length_before_read(self) -> None:
        class OversizedResponse:
            headers = {"Content-Length": "17"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size: int) -> bytes:
                self.fail("oversized declared response must not be read")

        response = OversizedResponse()
        response.fail = self.fail
        with unittest.mock.patch.object(
            structure_fetch_module,
            "MAX_STRUCTURE_DOWNLOAD_BYTES",
            16,
        ), unittest.mock.patch.object(
            structure_fetch_module.urllib.request,
            "urlopen",
            return_value=response,
        ):
            with self.assertRaises(structure_fetch_module._StructureDownloadTooLarge):
                structure_fetch_module._fetch_bytes("https://files.rcsb.org/download/1HSG.pdb", 1)

    def test_structure_fetch_does_not_import_processing_or_docking_adapters(self) -> None:
        import dockstart_core.structure_fetch as structure_fetch

        self.assertFalse(hasattr(structure_fetch, "rdkit_adapter"))
        self.assertFalse(hasattr(structure_fetch, "meeko_adapter"))
        self.assertFalse(hasattr(structure_fetch, "vina_adapter"))


if __name__ == "__main__":
    unittest.main()
