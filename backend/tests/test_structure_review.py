from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    create_project,
    get_run_preflight,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
)
from dockstart_core.structure_review import build_structure_review  # noqa: E402


def _pdb_line(
    record: str,
    serial: int,
    atom: str,
    residue: str,
    chain: str,
    number: int,
    element: str,
    *,
    altloc: str = "",
) -> str:
    return (
        f"{record:<6}{serial:5d} {atom:<4}{altloc:1}{residue:>3} {chain:1}{number:4d}    "
        f"{float(serial):8.3f}{0.0:8.3f}{0.0:8.3f}{1.0:6.2f}{0.0:6.2f}          {element:>2}\n"
    )


def _ligand_sdf() -> str:
    return """Charged two-component ligand
DockStart

  3  1  0  0  0  0            999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.3000    0.0000    0.0000 N   0  0  1  0  0  0  0  0  0  0  0  0
    5.0000    0.0000    0.0000 Cl  0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  1  0  0  0
M  CHG  2   2   1   3  -1
M  END
> <PUBCHEM_TOTAL_CHARGE>
0

$$$$
"""


class StructureReviewTests(unittest.TestCase):
    def test_reports_receptor_records_and_ligand_chemistry_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "raw").mkdir()
            (root / "prepared").mkdir()
            (root / "preparation" / "ligand_001").mkdir(parents=True)
            receptor = root / "raw" / "receptor.pdb"
            receptor.write_text(
                _pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C")
                + _pdb_line("ATOM", 2, "CA", "SER", "A", 2, "C", altloc="A")
                + _pdb_line("HETATM", 3, "O", "HOH", "A", 10, "O")
                + _pdb_line("HETATM", 4, "ZN", "ZN", "A", 11, "ZN")
                + _pdb_line("HETATM", 5, "C1", "FAD", "A", 12, "C")
                + _pdb_line("ATOM", 6, "CB", "ALA", "A", 1, "C"),
                encoding="utf-8",
            )
            ligand_raw = root / "raw" / "ligand.sdf"
            ligand_raw.write_text(_ligand_sdf(), encoding="utf-8")
            ligand_pdbqt = root / "prepared" / "ligand.pdbqt"
            ligand_pdbqt.write_text(
                "REMARK SMILES C[NH3+].[Cl-]\n"
                "REMARK SMILES IDX 1 1 2 2 3 3\n"
                + _pdb_line("HETATM", 1, "C1", "LIG", "B", 1, "C").rstrip("\n")
                + "  0.100 C\n"
                + _pdb_line("HETATM", 2, "N1", "LIG", "B", 1, "N").rstrip("\n")
                + " -0.100 N\n"
                + "ROOT\nTORSDOF 1\n",
                encoding="utf-8",
            )
            metadata = root / "preparation" / "ligand_001" / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "prep_id": "ligand_001",
                        "method": "rdkit_meeko",
                        "status": "finished",
                        "rdkit_version": "2026.03.3",
                        "meeko_version": "0.7.1",
                    },
                ),
                encoding="utf-8",
            )

            review = build_structure_review(
                root,
                receptor_file="",
                ligand_file="prepared/ligand.pdbqt",
                receptor_raw_file="raw/receptor.pdb",
                ligand_raw_file="raw/ligand.sdf",
                ligand_metadata_file="preparation/ligand_001/metadata.json",
            )

            self.assertFalse(review["scientific_validation"])
            self.assertEqual(review["receptor"]["chains"], ["A"])
            self.assertEqual(len(review["receptor"]["interrupted_residues"]), 1)
            self.assertEqual(review["receptor"]["water_residue_count"], 1)
            self.assertEqual(review["receptor"]["metals"][0]["element"], "ZN")
            self.assertEqual(review["receptor"]["nonstandard_residues"][0]["residue_name"], "FAD")
            self.assertEqual(review["receptor"]["alternate_locations"], ["A"])
            self.assertEqual(review["ligand"]["raw"]["heavy_atom_count"], 3)
            self.assertEqual(review["ligand"]["raw"]["formal_charge"], 0)
            self.assertEqual(review["ligand"]["raw"]["fragment_count"], 2)
            self.assertEqual(review["ligand"]["pdbqt"]["torsdof"], 1)
            self.assertEqual(review["ligand"]["pdbqt"]["formal_charge"], 0)
            self.assertEqual(review["provenance"]["ligand"]["meeko_version"], "0.7.1")
            checks = {item["key"]: item for item in review["checks"]}
            self.assertEqual(checks["receptor_continuity"]["status"], "warning")
            self.assertEqual(checks["ligand_fragments"]["status"], "warning")
            self.assertEqual(checks["ligand_tautomer"]["status"], "unknown")

    def test_run_preflight_exposes_non_blocking_structure_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            created = create_project("review_project", temp_dir)
            project_dir = Path(created["project_dir"])
            receptor_source = Path(temp_dir) / "receptor.pdbqt"
            ligand_source = Path(temp_dir) / "ligand.pdbqt"
            receptor_source.write_text(_pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C"), encoding="utf-8")
            ligand_source.write_text(
                "REMARK SMILES CCO\n"
                + _pdb_line("HETATM", 1, "C1", "LIG", "B", 1, "C").rstrip("\n")
                + "  0.000 C\nROOT\nTORSDOF 0\n",
                encoding="utf-8",
            )
            self.assertTrue(import_receptor_pdbqt(str(project_dir), str(receptor_source))["ok"])
            self.assertTrue(import_ligand_pdbqt(str(project_dir), str(ligand_source))["ok"])
            vina = ToolCheckResult(
                key="vina",
                name="AutoDock Vina",
                status="ok",
                version="1.2.7",
                path="mock-vina",
                message="可用",
                source="auto",
            )

            with patch("dockstart_core.project.vina_adapter.detect", return_value=vina):
                response = get_run_preflight(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertTrue(response["ready"])
            self.assertFalse(response["structure_review"]["scientific_validation"])
            keys = {item["key"] for item in response["checks"]}
            self.assertIn("receptor_structure_review", keys)
            self.assertIn("ligand_structure_review", keys)
            self.assertTrue(all(not item["blocking"] for item in response["checks"] if item["key"].endswith("structure_review")))


if __name__ == "__main__":
    unittest.main()
