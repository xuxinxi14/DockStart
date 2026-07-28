from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import viewer  # noqa: E402
from dockstart_core.project import create_project, get_box_params  # noqa: E402


class ViewerTests(unittest.TestCase):
    def _create_project(self, temp_dir: str) -> Path:
        response = create_project("viewer_project", temp_dir)
        self.assertTrue(response["ok"])
        return Path(response["project_dir"])

    def _read_project_json(self, project_dir: Path) -> dict:
        return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

    def _write_project_json(self, project_dir: Path, data: dict) -> None:
        (project_dir / "project.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _pdbqt_atom(
        serial: int,
        name: str,
        atom_type: str,
        x: float,
        y: float,
        z: float,
        *,
        residue: str = "LIG",
        charge: float = 0.0,
    ) -> str:
        return (
            f"{'ATOM':<6}{serial:>5} "
            f"{name:<4} "
            f"{residue:>3} "
            f"L{1:>4}    "
            f"{x:>8.3f}{y:>8.3f}{z:>8.3f}"
            f"{1.0:>6.2f}{0.0:>6.2f}    "
            f"{charge:>6.3f} {atom_type:>2}"
        )

    def _create_local_only_pair_run(
        self,
        project_dir: Path,
        *,
        modern: bool,
        flexible: bool = False,
    ) -> dict[str, Path]:
        run_id = "run_001"
        run_dir = project_dir / "runs" / run_id
        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir(parents=True)
        receptor_path = inputs_dir / "receptor.pdbqt"
        input_path = inputs_dir / "ligand.pdbqt"
        flex_path = inputs_dir / "flex.pdbqt"
        optimized_path = run_dir / "optimized.pdbqt"
        receptor_path.write_text(
            self._pdbqt_atom(
                1,
                "CA",
                "C",
                10.0,
                11.0,
                12.0,
                residue="REC",
            )
            + "\n",
            encoding="utf-8",
        )
        input_path.write_text(
            "\n".join(
                [
                    "ROOT",
                    self._pdbqt_atom(1, "C1", "C", 0.0, 0.0, 0.0, charge=0.1),
                    self._pdbqt_atom(2, "O1", "OA", 2.0, 0.0, 0.0, charge=-0.1),
                    "ENDROOT",
                    "TORSDOF 0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        optimized_lines = [
            "ROOT",
            self._pdbqt_atom(1, "C1", "C", 1.0, 0.0, 0.0, charge=0.1),
            self._pdbqt_atom(2, "O1", "OA", 3.0, 0.0, 0.0, charge=-0.1),
            "ENDROOT",
        ]
        if flexible:
            flex_path.write_text(
                "\n".join(
                    [
                        "BEGIN_RES TYR A 42",
                        "ROOT",
                        self._pdbqt_atom(
                            101,
                            "CB",
                            "C",
                            8.0,
                            8.0,
                            8.0,
                            residue="TYR",
                        ),
                        "ENDROOT",
                        "END_RES TYR A 42",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            optimized_lines.extend(
                [
                    "BEGIN_RES TYR A 42",
                    "ROOT",
                    self._pdbqt_atom(
                        101,
                        "CB",
                        "C",
                        9.0,
                        8.0,
                        8.0,
                        residue="TYR",
                    ),
                    "ENDROOT",
                    "END_RES TYR A 42",
                ]
            )
        optimized_lines.append("TORSDOF 0")
        optimized_path.write_text(
            "\n".join(optimized_lines) + "\n",
            encoding="utf-8",
        )

        receptor_relative = f"runs/{run_id}/inputs/receptor.pdbqt"
        input_relative = f"runs/{run_id}/inputs/ligand.pdbqt"
        flex_relative = f"runs/{run_id}/inputs/flex.pdbqt"
        optimized_relative = f"runs/{run_id}/optimized.pdbqt"

        def sha256(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        metadata: dict[str, object] = {
            "run_id": run_id,
            "status": "finished",
            "run_mode": "local_only",
            "output_file": optimized_relative,
            "pose_file": optimized_relative,
        }
        if modern:
            metadata.update(
                {
                    "execution_plan": {
                        "schema_version": 2,
                        "kind": "local_only_with_baseline",
                    },
                    "execution_phases": {
                        "input_score": {"status": "finished"},
                        "local_optimization": {"status": "finished"},
                    },
                    "input_sha256": {
                        "receptor": sha256(receptor_path),
                        "ligand": sha256(input_path),
                        **({"flex": sha256(flex_path)} if flexible else {}),
                    },
                    "snapshots": {
                        "inputs": {
                            "receptor": {
                                "relative_path": receptor_relative,
                                "sha256": sha256(receptor_path),
                            },
                            "ligand": {
                                "relative_path": input_relative,
                                "sha256": sha256(input_path),
                            },
                            **(
                                {
                                    "flex": {
                                        "relative_path": flex_relative,
                                        "sha256": sha256(flex_path),
                                    }
                                }
                                if flexible
                                else {}
                            ),
                        },
                    },
                    "artifacts": {
                        "out": {
                            "relative_path": optimized_relative,
                            "sha256": sha256(optimized_path),
                        },
                    },
                    "docking_protocol": {
                        "mode": "flexible" if flexible else "rigid",
                        "receptor_mode": "flexible" if flexible else "rigid",
                    },
                }
            )
        (run_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths = {
            "receptor": receptor_path,
            "input": input_path,
            "optimized": optimized_path,
        }
        if flexible:
            paths["flex"] = flex_path
        return paths

    def test_missing_project_fields_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "legacy_project"
            project_dir.mkdir()
            (project_dir / "project.json").write_text(
                json.dumps(
                    {
                        "project_name": "legacy_project",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                        "project_dir": str(project_dir),
                    }
                ),
                encoding="utf-8",
            )

            response = viewer.get_viewer_file_status(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["files"]["receptor_raw"]["message"], "受体原始文件 尚未记录在 project.json 中。")

    def test_receptor_raw_file_status_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_file = project_dir / "raw" / "receptor.pdb"
            raw_file.write_text("ATOM      1  C   ALA A   1       0.000   0.000   0.000\n", encoding="utf-8")
            data = self._read_project_json(project_dir)
            data["receptor"]["raw_file"] = "raw/receptor.pdb"
            self._write_project_json(project_dir, data)

            response = viewer.get_viewer_file_status(str(project_dir))

            receptor = response["files"]["receptor_raw"]
            self.assertTrue(receptor["ok"])
            self.assertEqual(receptor["format"], "pdb")

    def test_prepared_receptor_and_ligand_can_be_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            (project_dir / "prepared" / "receptor.pdbqt").write_text("REMARK receptor\n", encoding="utf-8")
            (project_dir / "prepared" / "ligand.pdbqt").write_text("REMARK ligand\n", encoding="utf-8")
            data = self._read_project_json(project_dir)
            data["receptor"]["file"] = "prepared/receptor.pdbqt"
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            self._write_project_json(project_dir, data)

            receptor = viewer.load_structure_for_viewer(str(project_dir), "receptor_prepared")
            ligand = viewer.load_structure_for_viewer(str(project_dir), "ligand_prepared")

            self.assertTrue(receptor["ok"])
            self.assertIn("REMARK receptor", receptor["content"])
            self.assertTrue(ligand["ok"])
            self.assertIn("REMARK ligand", ligand["content"])

    def test_meeko_ligand_is_not_truncated_at_endroot_in_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            ligand_path = project_dir / "prepared" / "ligand.pdbqt"
            pdbqt = (
                "ROOT\n"
                "ATOM      1  N   UNL     1       3.732   1.440   0.000  1.00  0.00    -0.384 N \n"
                "ATOM      2  N   UNL     1       2.000   1.440   0.000  1.00  0.00    -0.284 NA\n"
                "ATOM      3  C   UNL     1       2.866   0.940   0.000  1.00  0.00     0.122 C \n"
                "ENDROOT\n"
                "BRANCH   3   4\n"
                "ATOM      4  C   UNL     1       2.866  -0.060   0.000  1.00  0.00     0.016 A \n"
                "ATOM      5  C   UNL     1       2.000  -0.560   0.000  1.00  0.00     0.012 A \n"
                "ATOM      6  C   UNL     1       3.732  -0.560   0.000  1.00  0.00     0.012 A \n"
                "ENDBRANCH   3   4\n"
                "TORSDOF 1\n"
            )
            ligand_path.write_text(pdbqt, encoding="utf-8")
            data = self._read_project_json(project_dir)
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            self._write_project_json(project_dir, data)

            response = viewer.load_structure_for_viewer(str(project_dir), "ligand_prepared")

            self.assertTrue(response["ok"])
            self.assertEqual(response["format"], "pdb")
            self.assertEqual(
                len([line for line in response["content"].splitlines() if line.startswith("ATOM")]),
                6,
            )
            self.assertNotIn("ENDROOT", response["content"])
            self.assertNotIn("ENDBRANCH", response["content"])
            self.assertEqual(ligand_path.read_text(encoding="utf-8"), pdbqt)
            self.assertTrue(response["warnings"])

    def test_prepared_ligand_reuses_matching_raw_sdf_for_consistent_display(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_path = project_dir / "raw" / "ligand.sdf"
            raw_content = (
                "DockStart display ligand\n"
                "  DockStart\n\n"
                "  2  1  0  0  0  0            999 V2000\n"
                "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "    1.2000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "  1  2  1  0  0  0  0\n"
                "M  END\n$$$$\n"
            )
            raw_path.write_text(raw_content, encoding="utf-8")
            prepared_path = project_dir / "prepared" / "ligand.pdbqt"
            prepared_path.write_text(
                "ROOT\n"
                "ATOM      1  C   UNL     1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
                "ATOM      2  O   UNL     1       1.200   0.000   0.000  1.00  0.00     0.000 OA\n"
                "ENDROOT\nTORSDOF 0\n",
                encoding="utf-8",
            )
            data = self._read_project_json(project_dir)
            data["ligand"]["raw_file"] = "raw/ligand.sdf"
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            data["preparation"]["ligand"].update(
                {
                    "status": "finished",
                    "input_file": "raw/ligand.sdf",
                    "output_file": "prepared/ligand.pdbqt",
                }
            )
            self._write_project_json(project_dir, data)

            response = viewer.load_structure_for_viewer(str(project_dir), "ligand_prepared")

            self.assertTrue(response["ok"])
            self.assertEqual(response["format"], "sdf")
            self.assertEqual(response["relative_path"], "raw/ligand.sdf")
            self.assertEqual(response["content"], raw_content)
            self.assertTrue(any("原始 SDF/MOL" in warning for warning in response["warnings"]))

    def test_prepared_ligand_ignores_stale_raw_display_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            (project_dir / "raw" / "ligand.sdf").write_text("stale sdf\n", encoding="utf-8")
            prepared_path = project_dir / "prepared" / "ligand.pdbqt"
            prepared_path.write_text(
                "ATOM      1  C   UNL     1       0.000   0.000   0.000  1.00  0.00     0.000 C\n",
                encoding="utf-8",
            )
            data = self._read_project_json(project_dir)
            data["ligand"]["raw_file"] = "raw/ligand.sdf"
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            data["preparation"]["ligand"].update(
                {
                    "status": "finished",
                    "input_file": "raw/another-ligand.sdf",
                    "output_file": "prepared/ligand.pdbqt",
                }
            )
            self._write_project_json(project_dir, data)

            response = viewer.load_structure_for_viewer(str(project_dir), "ligand_prepared")

            self.assertTrue(response["ok"])
            self.assertEqual(response["format"], "pdb")
            self.assertEqual(response["relative_path"], "prepared/ligand.pdbqt")

    def test_modern_local_only_pair_loads_verified_run_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            paths = self._create_local_only_pair_run(project_dir, modern=True)
            (project_dir / "prepared" / "receptor.pdbqt").write_text(
                "REMARK mutable current receptor\n",
                encoding="utf-8",
            )
            (project_dir / "prepared" / "ligand.pdbqt").write_text(
                "REMARK mutable current ligand\n",
                encoding="utf-8",
            )

            response = viewer.load_local_only_pose_pair_for_viewer(
                str(project_dir),
                "run_001",
            )

            self.assertTrue(response["ok"], response)
            self.assertEqual(response["run_mode"], "local_only")
            self.assertEqual(response["integrity"]["status"], "verified")
            self.assertEqual(
                response["integrity"]["receptor_sha256"],
                hashlib.sha256(paths["receptor"].read_bytes()).hexdigest(),
            )
            self.assertEqual(
                response["integrity"]["input_sha256"],
                hashlib.sha256(paths["input"].read_bytes()).hexdigest(),
            )
            self.assertEqual(
                response["integrity"]["optimized_sha256"],
                hashlib.sha256(paths["optimized"].read_bytes()).hexdigest(),
            )
            self.assertEqual(response["coordinate_frame"]["source"], "run_snapshot")
            self.assertTrue(response["coordinate_frame"]["same_receptor_frame"])
            self.assertFalse(response["coordinate_frame"]["alignment_applied"])
            self.assertEqual(response["receptor"]["format"], "pdb")
            self.assertEqual(response["input"]["format"], "pdb")
            self.assertEqual(response["optimized"]["format"], "pdb")
            self.assertIn("  10.000  11.000  12.000", response["receptor"]["content"])
            self.assertIn("   0.000   0.000   0.000", response["input"]["content"])
            self.assertIn("   1.000   0.000   0.000", response["optimized"]["content"])
            self.assertNotIn("mutable current", response["receptor"]["content"])
            self.assertNotIn("mutable current", response["input"]["content"])
            self.assertNotIn("mutable current", response["optimized"]["content"])
            self.assertEqual(response["input"]["pose_kind"], "input")
            self.assertEqual(response["optimized"]["pose_kind"], "optimized")
            self.assertEqual(
                response["comparison"]["mapping_method"],
                "pdbqt_serial_full_identity",
            )
            self.assertAlmostEqual(
                response["comparison"]["heavy_atom_rmsd_no_alignment_angstrom"],
                1.0,
            )

    def test_flexible_local_only_pair_returns_verified_flex_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            paths = self._create_local_only_pair_run(
                project_dir,
                modern=True,
                flexible=True,
            )

            response = viewer.load_local_only_pose_pair_for_viewer(
                str(project_dir),
                "run_001",
            )

            self.assertTrue(response["ok"], response)
            self.assertEqual(response["integrity"]["status"], "verified")
            self.assertEqual(
                response["integrity"]["flex_input_sha256"],
                hashlib.sha256(paths["flex"].read_bytes()).hexdigest(),
            )
            self.assertIn("   8.000   8.000   8.000", response["flex_receptor_input"]["content"])
            self.assertIn(
                "   9.000   8.000   8.000",
                response["flex_receptor_optimized"]["content"],
            )
            self.assertNotIn(" TYR ", response["optimized"]["content"])
            self.assertEqual(
                response["comparison"]["excluded_atom_counts"][
                    "optimized_flexible_receptor"
                ],
                1,
            )

    def test_modern_local_only_pair_rejects_changed_snapshots(self) -> None:
        for changed_key in ("receptor", "input", "optimized"):
            with self.subTest(changed_key=changed_key), tempfile.TemporaryDirectory() as temp_dir:
                project_dir = self._create_project(temp_dir)
                paths = self._create_local_only_pair_run(project_dir, modern=True)
                with paths[changed_key].open("a", encoding="utf-8") as handle:
                    handle.write("REMARK changed after run\n")

                response = viewer.load_local_only_pose_pair_for_viewer(
                    str(project_dir),
                    "run_001",
                )

                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "VIEWER_LOCAL_POSE_PAIR_HASH_MISMATCH",
                )

    def test_flexible_local_only_pair_rejects_changed_flex_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            paths = self._create_local_only_pair_run(
                project_dir,
                modern=True,
                flexible=True,
            )
            with paths["flex"].open("a", encoding="utf-8") as handle:
                handle.write("REMARK changed after run\n")

            response = viewer.load_local_only_pose_pair_for_viewer(
                str(project_dir),
                "run_001",
            )

            self.assertFalse(response["ok"])
            self.assertEqual(
                response["error"]["code"],
                "VIEWER_LOCAL_POSE_PAIR_HASH_MISMATCH",
            )

    def test_legacy_local_only_pair_detects_flex_from_run_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._create_local_only_pair_run(
                project_dir,
                modern=False,
                flexible=True,
            )

            response = viewer.load_local_only_pose_pair_for_viewer(
                str(project_dir),
                "run_001",
            )

            self.assertTrue(response["ok"], response)
            self.assertTrue(response["coordinate_frame"]["flexible_receptor"])
            self.assertIn("flex_receptor_input", response)
            self.assertIn("flex_receptor_optimized", response)
            self.assertEqual(response["integrity"]["status"], "legacy_unverified")

    def test_legacy_local_only_pair_is_explicitly_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._create_local_only_pair_run(project_dir, modern=False)

            response = viewer.load_local_only_pose_pair_for_viewer(
                str(project_dir),
                "run_001",
            )

            self.assertTrue(response["ok"], response)
            self.assertEqual(response["integrity"]["status"], "legacy_unverified")
            self.assertEqual(
                response["integrity"]["source"],
                "current_file_observation",
            )
            self.assertTrue(
                any("历史 local_only run" in warning for warning in response["warnings"])
            )
            self.assertTrue(response["comparison"]["ok"])

    def test_docking_pose_removes_pdbqt_branch_terminators_for_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text(
                "MODEL 1\n"
                "ROOT\n"
                "ATOM      1  C   UNL     1       0.000   0.000   0.000  1.00  0.00     0.000 C \n"
                "ENDROOT\n"
                "BRANCH   1   2\n"
                "ATOM      2  C   UNL     1       1.400   0.000   0.000  1.00  0.00     0.000 C \n"
                "ENDBRANCH   1   2\n"
                "TORSDOF 1\n"
                "ENDMDL\n",
                encoding="utf-8",
            )

            response = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 1)

            self.assertTrue(response["ok"])
            self.assertEqual(response["format"], "pdb")
            self.assertEqual(
                len([line for line in response["content"].splitlines() if line.startswith("ATOM")]),
                2,
            )
            self.assertNotIn("ENDROOT", response["content"])
            self.assertNotIn("ENDBRANCH", response["content"])

    def test_hydrated_pose_keeps_water_and_displays_w_as_oxygen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            retained_relative = "runs/run_001/hydrated_retained.pdbqt"
            (run_dir / "hydrated_retained.pdbqt").write_text(
                "\n".join(
                    [
                        "MODEL 1",
                        self._pdbqt_atom(1, "C1", "C", 0.0, 0.0, 0.0),
                        self._pdbqt_atom(
                            2,
                            "W",
                            "W",
                            2.0,
                            0.0,
                            0.0,
                            residue="WAT",
                        ),
                        "ENDMDL",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "run_001",
                        "status": "finished",
                        "protocol_id": "hydrated_ad4_experimental",
                        "output_file": "runs/run_001/out.pdbqt",
                        "pose_file": retained_relative,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            response = viewer.load_docking_pose_for_viewer(
                str(project_dir),
                "run_001",
                1,
            )

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["relative_path"], retained_relative)
        self.assertEqual(response["format"], "pdb")
        atom_lines = [
            line
            for line in response["content"].splitlines()
            if line.startswith("ATOM")
        ]
        self.assertEqual(len(atom_lines), 2)
        self.assertEqual(atom_lines[1][12:16].strip(), "O")
        self.assertEqual(atom_lines[1][76:78].strip(), "O")
        self.assertTrue(
            any("W 原子" in warning for warning in response["warnings"])
        )

    def test_docking_pose_reuses_raw_ligand_bonds_with_vina_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_content = (
                "DockStart ligand\n"
                "  DockStart\n\n"
                "  5  4  0  0  0  0            999 V2000\n"
                "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "    1.2000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "    0.0000    1.3000    0.0000 N   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "   -0.8000   -0.6000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "    0.0000    2.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "  1  2  2  0  0  0  0\n"
                "  1  3  1  0  0  0  0\n"
                "  1  4  1  0  0  0  0\n"
                "  3  5  1  0  0  0  0\n"
                "M  END\n$$$$\n"
            )
            raw_path = project_dir / "raw" / "ligand.sdf"
            raw_path.write_text(raw_content, encoding="utf-8")
            prepared_content = (
                "ATOM      1  O   UNL     1       1.200   0.000   0.000  1.00  0.00     0.000 OA\n"
                "ATOM      2  C   UNL     1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
                "ATOM      3  N   UNL     1       0.000   1.300   0.000  1.00  0.00     0.000 N\n"
                "ATOM      4  H   UNL     1       0.000   2.000   0.000  1.00  0.00     0.000 HD\n"
            )
            (project_dir / "prepared" / "ligand.pdbqt").write_text(prepared_content, encoding="utf-8")
            run_dir = project_dir / "runs" / "run_001"
            (run_dir / "inputs").mkdir(parents=True)
            (run_dir / "inputs" / "ligand.pdbqt").write_text(prepared_content, encoding="utf-8")
            (run_dir / "out.pdbqt").write_text(
                "MODEL 1\n"
                "ATOM      1  O   UNL     1      11.200  20.000  30.000  1.00  0.00     0.000 OA\n"
                "ATOM      2  C   UNL     1      10.000  20.000  30.000  1.00  0.00     0.000 C\n"
                "ATOM      3  N   UNL     1      10.000  21.300  30.000  1.00  0.00     0.000 N\n"
                "ATOM      4  H   UNL     1      10.000  22.000  30.000  1.00  0.00     0.000 HD\n"
                "ENDMDL\n",
                encoding="utf-8",
            )
            data = self._read_project_json(project_dir)
            data["ligand"]["raw_file"] = "raw/ligand.sdf"
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            data["preparation"]["ligand"].update(
                {
                    "status": "finished",
                    "input_file": "raw/ligand.sdf",
                    "output_file": "prepared/ligand.pdbqt",
                }
            )
            self._write_project_json(project_dir, data)

            response = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 1)

            self.assertTrue(response["ok"])
            self.assertEqual(response["format"], "sdf")
            self.assertIn("  3  2  0  0  0  0            999 V2000", response["content"])
            self.assertIn("   10.0000   20.0000   30.0000 C", response["content"])
            self.assertIn("  1  2  2  0  0  0  0", response["content"])
            self.assertNotIn(" H  ", response["content"])
            self.assertTrue(any("原始 SDF/MOL 键拓扑" in warning for warning in response["warnings"]))

    def test_docking_pose_falls_back_when_raw_atom_mapping_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            (project_dir / "raw" / "ligand.sdf").write_text(
                "Mismatch\n  DockStart\n\n"
                "  1  0  0  0  0  0            999 V2000\n"
                "   99.0000   99.0000   99.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "M  END\n$$$$\n",
                encoding="utf-8",
            )
            (project_dir / "prepared" / "ligand.pdbqt").write_text(
                "ATOM      1  C   UNL     1       0.000   0.000   0.000  1.00  0.00     0.000 C\n",
                encoding="utf-8",
            )
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text(
                "MODEL 1\n"
                "ATOM      1  C   UNL     1       5.000   6.000   7.000  1.00  0.00     0.000 C\n"
                "ENDMDL\n",
                encoding="utf-8",
            )
            data = self._read_project_json(project_dir)
            data["ligand"]["raw_file"] = "raw/ligand.sdf"
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            data["preparation"]["ligand"].update(
                {
                    "status": "finished",
                    "input_file": "raw/ligand.sdf",
                    "output_file": "prepared/ligand.pdbqt",
                }
            )
            self._write_project_json(project_dir, data)

            response = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 1)

            self.assertTrue(response["ok"])
            self.assertEqual(response["format"], "pdb")
            self.assertIn("ATOM      1", response["content"])

    def test_docking_output_can_be_listed_and_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text(
                "MODEL 1\nREMARK pose 1\nENDMDL\nMODEL 2\nREMARK pose 2\nENDMDL\n",
                encoding="utf-8",
            )
            data = self._read_project_json(project_dir)
            data["runs"] = [{"run_id": "run_001", "status": "finished"}]
            self._write_project_json(project_dir, data)

            status = viewer.get_viewer_file_status(str(project_dir))
            poses = viewer.list_docking_poses(str(project_dir), "run_001")
            pose = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 2)

            self.assertTrue(status["files"]["docking_output"]["ok"])
            self.assertEqual([item["mode"] for item in poses["poses"]], [1, 2])
            self.assertTrue(pose["ok"])
            self.assertIn("pose 2", pose["content"])

    def test_docking_pose_scores_are_matched_by_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text(
                "MODEL 1\nREMARK pose 1\nENDMDL\nMODEL 2\nREMARK pose 2\nENDMDL\n",
                encoding="utf-8",
            )
            (run_dir / "scores.csv").write_text(
                "mode,affinity_kcal_mol,rmsd_lb,rmsd_ub\n1,-7.1,0,0\n2,-6.4,1.2,2.3\n",
                encoding="utf-8",
            )

            poses = viewer.list_docking_poses(str(project_dir), "run_001")
            pose = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 2)

            self.assertTrue(poses["ok"])
            self.assertEqual(poses["poses"][0]["affinity_kcal_mol"], -7.1)
            self.assertEqual(poses["poses"][1]["rmsd_ub"], 2.3)
            self.assertEqual(pose["score"]["affinity_kcal_mol"], -6.4)

    def test_missing_scores_csv_keeps_pose_visible_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text("MODEL 1\nREMARK pose 1\nENDMDL\n", encoding="utf-8")

            poses = viewer.list_docking_poses(str(project_dir), "run_001")

            self.assertTrue(poses["ok"])
            self.assertEqual(len(poses["poses"]), 1)
            self.assertTrue(poses["warnings"])

    def test_docking_output_without_model_is_single_pose(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text("REMARK single pose\nATOM\n", encoding="utf-8")

            poses = viewer.list_docking_poses(str(project_dir), "run_001")

            self.assertTrue(poses["ok"])
            self.assertEqual(len(poses["poses"]), 1)
            self.assertEqual(poses["poses"][0]["mode"], 1)

    def test_missing_pose_mode_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text("MODEL 1\nREMARK pose 1\nENDMDL\n", encoding="utf-8")

            response = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 9)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VIEWER_POSE_MODE_NOT_FOUND")

    def test_oversized_file_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_file = project_dir / "raw" / "big.pdb"
            raw_file.write_text("0123456789abcdef", encoding="utf-8")

            original_limit = viewer.MAX_VIEWER_FILE_BYTES
            viewer.MAX_VIEWER_FILE_BYTES = 8
            try:
                response = viewer.validate_viewer_file(str(project_dir), "raw/big.pdb")
            finally:
                viewer.MAX_VIEWER_FILE_BYTES = original_limit

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VIEWER_FILE_TOO_LARGE")

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            outside = Path(temp_dir) / "outside.pdb"
            outside.write_text("ATOM\n", encoding="utf-8")
            data = self._read_project_json(project_dir)
            data["receptor"]["raw_file"] = "../outside.pdb"
            self._write_project_json(project_dir, data)

            response = viewer.load_structure_for_viewer(str(project_dir), "receptor_raw")

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VIEWER_FILE_OUTSIDE_PROJECT")

    def test_viewer_does_not_call_external_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            with patch.object(subprocess, "run") as run_mock:
                response = viewer.get_viewer_file_status(str(project_dir))

            self.assertTrue(response["ok"])
            run_mock.assert_not_called()

    def test_default_box_visualization_payload_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            response = viewer.get_box_visualization(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["visualization"]["unit"], "angstrom")
            self.assertEqual(response["visualization"]["center_x"], 0.0)
            self.assertEqual(response["visualization"]["size_x"], 20.0)
            self.assertEqual(len(response["visualization"]["corners"]), 8)
            self.assertEqual(response["visualization"]["viewer_box_payload"]["dimensions"]["w"], 20.0)

    def test_invalid_box_size_is_rejected_for_visualization_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            response = viewer.update_box_from_visualization(
                str(project_dir),
                {
                    "center_x": 0,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 0,
                    "size_y": 20,
                    "size_z": 20,
                },
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "BOX_SIZE_NOT_POSITIVE")

    def test_update_box_from_visualization_updates_project_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            response = viewer.update_box_from_visualization(
                str(project_dir),
                {
                    "center_x": -1.5,
                    "center_y": 2,
                    "center_z": 3,
                    "size_x": 16,
                    "size_y": 18,
                    "size_z": 22,
                },
            )
            box_response = get_box_params(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["visualization"]["center_x"], -1.5)
            self.assertEqual(box_response["box"]["center_x"], -1.5)
            self.assertEqual(box_response["box"]["size_z"], 22)

    def test_large_box_visualization_returns_warning_but_allows_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            response = viewer.update_box_from_visualization(
                str(project_dir),
                {
                    "center_x": 0,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 61,
                    "size_y": 20,
                    "size_z": 20,
                },
            )

            self.assertTrue(response["ok"])
            self.assertTrue(response["warnings"])

    def test_box_visualization_does_not_call_external_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            with patch.object(subprocess, "run") as run_mock:
                response = viewer.get_box_visualization(str(project_dir))

            self.assertTrue(response["ok"])
            run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
