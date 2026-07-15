from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.project import create_project, load_project  # noqa: E402
from dockstart_core.structure_fetch import (  # noqa: E402
    fetch_pdb_structure,
    search_pubchem_candidates,
    search_rcsb_candidates,
    validate_search_limit,
)


def _entry_payload(pdb_id: str, title: str, resolution: float = 2.1) -> dict[str, object]:
    return {
        "rcsb_id": pdb_id,
        "struct": {"title": title},
        "exptl": [{"method": "X-RAY DIFFRACTION"}],
        "rcsb_entry_info": {
            "experimental_method": "X-ray",
            "resolution_combined": [resolution],
            "polymer_entity_count": 1,
            "nonpolymer_entity_count": 2,
            "deposited_atom_count": 4710,
        },
        "rcsb_accession_info": {"initial_release_date": "2001-04-18T00:00:00+00:00"},
        "struct_keywords": {"text": "KINASE, INHIBITOR"},
    }


class StructureSearchTests(unittest.TestCase):
    def _create_project(self, temp_dir: str) -> Path:
        response = create_project("candidate_project", temp_dir)
        self.assertTrue(response["ok"], response)
        return Path(response["project_dir"])

    def test_search_limit_is_bounded(self) -> None:
        self.assertTrue(validate_search_limit("8")["ok"])
        self.assertEqual(validate_search_limit("many")["error"]["code"], "STRUCTURE_SEARCH_LIMIT_INVALID")
        self.assertEqual(validate_search_limit(0)["error"]["code"], "STRUCTURE_SEARCH_LIMIT_OUT_OF_RANGE")
        self.assertEqual(validate_search_limit(21)["error"]["code"], "STRUCTURE_SEARCH_LIMIT_OUT_OF_RANGE")

    def test_rcsb_exact_id_returns_metadata_and_requires_selection(self) -> None:
        def fetcher(url: str, _timeout: int) -> bytes:
            self.assertEqual(url, "https://data.rcsb.org/rest/v1/core/entry/1IEP")
            return json.dumps(_entry_payload("1IEP", "C-ABL WITH IMATINIB")).encode()

        result = search_rcsb_candidates("1iep", limit=5, fetcher=fetcher)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["selection_required"])
        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(result["candidates"][0]["candidate_id"], "rcsb:1IEP")
        self.assertEqual(result["candidates"][0]["metadata"]["resolution_angstrom"], 2.1)
        self.assertEqual(result["candidates"][0]["selection"]["download_command"], "fetch-pdb")
        self.assertEqual(result["candidates"][0]["selection"]["pdb_id"], "1IEP")

    def test_rcsb_keyword_returns_requested_candidates_in_remote_order(self) -> None:
        requested_rows: list[int] = []

        def fetcher(url: str, _timeout: int) -> bytes:
            if url.startswith("https://search.rcsb.org/"):
                encoded_query = parse_qs(urlparse(url).query)["json"][0]
                request = json.loads(encoded_query)
                requested_rows.append(request["request_options"]["paginate"]["rows"])
                return json.dumps(
                    {
                        "total_count": 20,
                        "result_set": [
                            {"identifier": "1IEP", "score": 1.0},
                            {"identifier": "3PTB", "score": 0.9},
                        ],
                    }
                ).encode()
            self.assertTrue(url.startswith("https://data.rcsb.org/graphql?query="))
            return json.dumps(
                {
                    "data": {
                        "entries": [
                            _entry_payload("1IEP", "TITLE 1IEP"),
                            _entry_payload("3PTB", "TITLE 3PTB"),
                        ]
                    }
                }
            ).encode()

        result = search_rcsb_candidates("kinase inhibitor", limit=2, query_type="keyword", fetcher=fetcher)

        self.assertTrue(result["ok"], result)
        self.assertEqual(requested_rows, [2])
        self.assertEqual(result["total_count"], 20)
        self.assertTrue(result["truncated"])
        self.assertEqual([item["source_id"] for item in result["candidates"]], ["1IEP", "3PTB"])
        self.assertNotIn("selected_candidate_id", result)

    def test_rcsb_keyword_keeps_candidate_when_one_metadata_request_fails(self) -> None:
        def fetcher(url: str, _timeout: int) -> bytes:
            if url.startswith("https://search.rcsb.org/"):
                return json.dumps(
                    {"total_count": 1, "result_set": [{"identifier": "1IEP", "score": 1.0}]}
                ).encode()
            self.assertTrue(url.startswith("https://data.rcsb.org/graphql?query="))
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)

        result = search_rcsb_candidates("kinase", limit=1, query_type="keyword", fetcher=fetcher)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["candidates"][0]["metadata"]["metadata_status"], "unavailable")
        self.assertEqual(result["candidates"][0]["selection"]["pdb_id"], "1IEP")

    def test_pubchem_cid_returns_compound_properties(self) -> None:
        def fetcher(url: str, _timeout: int) -> bytes:
            self.assertIn("/compound/cid/2244/property/", url)
            return json.dumps(
                {
                    "PropertyTable": {
                        "Properties": [
                            {
                                "CID": 2244,
                                "Title": "Aspirin",
                                "MolecularFormula": "C9H8O4",
                                "MolecularWeight": "180.16",
                                "SMILES": "CC(=O)OC1=CC=CC=C1C(=O)O",
                                "InChIKey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                            }
                        ]
                    }
                }
            ).encode()

        result = search_pubchem_candidates("2244", limit=6, fetcher=fetcher)

        self.assertTrue(result["ok"], result)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["candidate_id"], "pubchem:2244")
        self.assertEqual(candidate["title"], "Aspirin")
        self.assertEqual(candidate["metadata"]["molecular_formula"], "C9H8O4")
        self.assertEqual(candidate["selection"]["query_type"], "cid")

    def test_pubchem_name_uses_official_autocomplete_candidates_without_default_selection(self) -> None:
        def fetcher(url: str, _timeout: int) -> bytes:
            self.assertIn("/rest/autocomplete/compound/imatinib/json?limit=3", url)
            return json.dumps(
                {
                    "status": {"code": 0},
                    "total": 3,
                    "dictionary_terms": {
                        "compound": ["Imatinib", "Imatinib mesylate", "N-Desmethyl Imatinib"]
                    },
                }
            ).encode()

        result = search_pubchem_candidates("imatinib", limit=3, query_type="name", fetcher=fetcher)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["selection_required"])
        self.assertEqual(result["returned_count"], 3)
        self.assertEqual(result["candidates"][1]["title"], "Imatinib mesylate")
        self.assertEqual(result["candidates"][1]["selection"]["query_type"], "name")
        self.assertEqual(result["candidates"][1]["metadata"]["metadata_status"], "resolves_on_selection")
        self.assertNotIn("selected_candidate_id", result)

    def test_search_returns_structured_error_for_invalid_json(self) -> None:
        result = search_pubchem_candidates("2244", fetcher=lambda _url, _timeout: b"not json")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "STRUCTURE_SEARCH_RESPONSE_INVALID")

    def test_user_can_download_second_rcsb_candidate_explicitly(self) -> None:
        def search_fetcher(url: str, _timeout: int) -> bytes:
            if url.startswith("https://search.rcsb.org/"):
                return json.dumps(
                    {
                        "total_count": 2,
                        "result_set": [
                            {"identifier": "1IEP", "score": 1.0},
                            {"identifier": "3PTB", "score": 0.9},
                        ],
                    }
                ).encode()
            return json.dumps(
                {
                    "data": {
                        "entries": [
                            _entry_payload("1IEP", "TITLE 1IEP"),
                            _entry_payload("3PTB", "TITLE 3PTB"),
                        ]
                    }
                }
            ).encode()

        search = search_rcsb_candidates("enzyme", limit=2, query_type="keyword", fetcher=search_fetcher)
        chosen = search["candidates"][1]["selection"]

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            download = fetch_pdb_structure(
                str(project_dir),
                chosen["pdb_id"],
                chosen["format"],
                fetcher=lambda _url, _timeout: b"HEADER SELECTED 3PTB\n",
            )
            project = load_project(str(project_dir))["project"]

        self.assertTrue(download["ok"], download)
        self.assertEqual(download["source_id"], "3PTB")
        self.assertEqual(project["receptor"]["source_id"], "3PTB")
        self.assertEqual(project["receptor"]["raw_file"], "raw/receptor_3PTB.pdb")


if __name__ == "__main__":
    unittest.main()
