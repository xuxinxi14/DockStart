from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters.reference_rmsd_worker import (  # noqa: E402
    _meeko_smiles_map,
    _pdbqt_pose_lines,
    load_reference_molecule,
)
from dockstart_core.reference_rmsd import calculate_reference_rmsd  # noqa: E402


class ReferenceRmsdWorkerParsingTests(unittest.TestCase):
    def test_worker_stdout_is_utf8_on_windows(self) -> None:
        worker = BACKEND_ROOT / "adapters" / "reference_rmsd_worker.py"
        completed = subprocess.run(
            [
                sys.executable,
                str(worker),
                "--pdbqt",
                "missing.pdbqt",
                "--reference",
                "missing.sdf",
                "--mode",
                "1",
            ],
            capture_output=True,
            check=False,
        )
        payload = json.loads(completed.stdout.decode("utf-8", errors="strict"))

        self.assertFalse(payload["ok"])
        self.assertIn("RMSD", payload["error"]["message"])
        self.assertTrue(any(ord(character) > 127 for character in payload["error"]["message"]))
        self.assertNotIn("\ufffd", payload["error"]["message"])

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

    def test_sdf_reader_supports_unicode_and_space_path(self) -> None:
        try:
            import rdkit  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("当前测试 Python 未安装 RDKit。")

        sdf = """DockStart
  DockStart

  1  0  0  0  0  0            999 V2000
    1.0000    2.0000    3.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
M  END
$$$$
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "中文 参考配体" / "共晶 配体.sdf"
            reference.parent.mkdir()
            reference.write_text(sdf, encoding="utf-8")

            molecule = load_reference_molecule(reference)

        self.assertEqual(molecule.GetNumAtoms(), 1)
        self.assertEqual(molecule.GetNumConformers(), 1)


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
