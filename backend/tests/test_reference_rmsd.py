from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters.reference_rmsd_worker import _meeko_smiles_map, _pdbqt_pose_lines  # noqa: E402
from dockstart_core.reference_rmsd import calculate_reference_rmsd  # noqa: E402


class ReferenceRmsdWorkerParsingTests(unittest.TestCase):
    def test_unwrapped_pdbqt_keeps_every_atom(self) -> None:
        text = """REMARK SMILES CO
REMARK SMILES IDX 1 1 2 2
ATOM      1  C   UNL     1       0.000   0.000   0.000  1.00  0.00     0.0 C
ATOM      2  O   UNL     1       1.200   0.000   0.000  1.00  0.00     0.0 OA
"""
        self.assertEqual(len(_pdbqt_pose_lines(text, 1)), 2)
        smiles, mapping = _meeko_smiles_map(text)
        self.assertEqual(smiles, "CO")
        self.assertEqual(mapping, {0: 1, 1: 2})

    def test_model_selection_uses_requested_pose(self) -> None:
        text = """MODEL 1
ATOM      1  C   UNL     1       0.000   0.000   0.000  1.00  0.00     0.0 C
ENDMDL
MODEL 2
ATOM      1  C   UNL     1       9.000   0.000   0.000  1.00  0.00     0.0 C
ENDMDL
"""
        self.assertIn("9.000", _pdbqt_pose_lines(text, 2)[0])


class ReferenceRmsdWorkflowTests(unittest.TestCase):
    def test_success_copies_reference_and_records_hashes_in_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text("REMARK SMILES C\n", encoding="utf-8")
            (run_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run_001",
                        "status": "finished",
                        "output_file": "runs/run_001/out.pdbqt",
                    }
                ),
                encoding="utf-8",
            )
            reference = root / "reference.sdf"
            reference.write_text("reference", encoding="utf-8")
            worker_result = {
                "ok": True,
                "mode": 1,
                "rmsd_angstrom": 1.25,
                "method": "RDKit GetBestRMS",
                "heavy_atom_count": 10,
                "rdkit_version": "test",
            }
            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(worker_result, ensure_ascii=False),
                stderr="",
            )
            python_tool = SimpleNamespace(
                status="ok",
                path="python.exe",
                source="configured",
                raw_error="",
            )
            with patch("dockstart_core.reference_rmsd.get_resolved_python", return_value=python_tool), patch(
                "dockstart_core.reference_rmsd.subprocess.run", return_value=completed
            ):
                response = calculate_reference_rmsd(str(root), "run_001", 1, str(reference))

            self.assertTrue(response["ok"])
            metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["reference_rmsd"]["rmsd_angstrom"], 1.25)
            self.assertEqual(len(metadata["reference_rmsd"]["reference_sha256"]), 64)
            copied = root / metadata["reference_rmsd"]["reference_file"]
            self.assertTrue(copied.is_file())


if __name__ == "__main__":
    unittest.main()
