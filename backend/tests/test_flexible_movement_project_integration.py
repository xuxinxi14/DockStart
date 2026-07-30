from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import project as project_module  # noqa: E402
from dockstart_core.flexible_movement import (  # noqa: E402
    canonical_flexible_movement_json,
)
from dockstart_core.project import (  # noqa: E402
    analyze_vina_run_results,
    build_markdown_report,
    create_project,
    load_scores_csv,
)


STANDARD_VINA_LOG = """AutoDock Vina v1.2.7

mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1        -8.7      0.0      0.0
   2        -8.2      1.532    2.145
"""


def _atom(
    serial: int,
    atom_name: str,
    atom_type: str,
    x: float,
    y: float,
    z: float,
    *,
    residue_name: str,
    chain_id: str,
    residue_number: int,
    partial_charge: float = 0.0,
) -> str:
    return (
        f"{'ATOM':<6}{serial:>5} "
        f"{atom_name:<4}"
        f"{'':1}"
        f"{residue_name:>3}"
        f" "
        f"{chain_id:1}"
        f"{residue_number:>4}"
        f"{'':1}"
        f"   "
        f"{x:>8.3f}{y:>8.3f}{z:>8.3f}"
        f"{1.0:>6.2f}{0.0:>6.2f}"
        f"    "
        f"{partial_charge:>6.3f}"
        f" "
        f"{atom_type:>2}"
    )


def _ligand(
    coordinates: dict[int, tuple[float, float, float]],
) -> str:
    return "\n".join(
        [
            "ROOT",
            _atom(
                1,
                "C1",
                "C",
                *coordinates[1],
                residue_name="LIG",
                chain_id="L",
                residue_number=1,
                partial_charge=0.1,
            ),
            _atom(
                2,
                "O1",
                "OA",
                *coordinates[2],
                residue_name="LIG",
                chain_id="L",
                residue_number=1,
                partial_charge=-0.2,
            ),
            "ENDROOT",
            "TORSDOF 0",
        ]
    ) + "\n"


def _flex(
    coordinates: dict[int, tuple[float, float, float]],
) -> str:
    return "\n".join(
        [
            "BEGIN_RES TYR A 42",
            "ROOT",
            _atom(
                10,
                "CA",
                "C",
                *coordinates[10],
                residue_name="TYR",
                chain_id="A",
                residue_number=42,
            ),
            _atom(
                11,
                "CB",
                "C",
                *coordinates[11],
                residue_name="TYR",
                chain_id="A",
                residue_number=42,
            ),
            "ENDROOT",
            "BRANCH 11 12",
            _atom(
                12,
                "OH",
                "OA",
                *coordinates[12],
                residue_name="TYR",
                chain_id="A",
                residue_number=42,
                partial_charge=-0.3,
            ),
            "ENDBRANCH 11 12",
            "END_RES TYR A 42",
        ]
    ) + "\n"


LIGAND_INPUT = _ligand(
    {
        1: (1.0, 0.0, 0.0),
        2: (-1.0, 0.0, 0.0),
    }
)
FLEX_INPUT = _flex(
    {
        10: (0.0, 0.0, 0.0),
        11: (1.0, 0.0, 0.0),
        12: (2.0, 0.0, 0.0),
    }
)


def _output_mode(
    mode: int,
    ligand_coordinates: dict[int, tuple[float, float, float]],
    flex_coordinates: dict[int, tuple[float, float, float]],
) -> str:
    return (
        f"MODEL {mode}\n"
        f"REMARK VINA RESULT: {-9.2 + mode / 2:.3f} 0.000 0.000\n"
        + _ligand(ligand_coordinates)
        + _flex(flex_coordinates)
        + "ENDMDL\n"
    )


VINA_OUTPUT = _output_mode(
    1,
    {1: (1.0, 2.0, 0.0), 2: (-1.0, 2.0, 0.0)},
    {10: (9.0, 0.0, 0.0), 11: (1.0, 1.0, 0.0), 12: (2.0, 2.0, 0.0)},
) + _output_mode(
    2,
    {1: (0.0, 1.0, 0.0), 2: (0.0, -1.0, 0.0)},
    {10: (19.0, 0.0, 0.0), 11: (1.0, 2.0, 0.0), 12: (2.0, 4.0, 0.0)},
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(path: Path, relative_path: str) -> dict[str, object]:
    return {
        "relative_path": relative_path,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


class FlexibleMovementProjectIntegrationTests(unittest.TestCase):
    maxDiff = None

    def _create_run(
        self,
        temp_dir: str,
        *,
        modern: bool,
        run_mode: str = "dock",
    ) -> tuple[Path, str]:
        created = create_project("flexible_project", temp_dir)
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])
        run_id = "run_001"
        run_dir = project_dir / "runs" / run_id
        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir(parents=True)

        receptor = inputs_dir / "receptor.pdbqt"
        ligand = inputs_dir / "ligand.pdbqt"
        flex = inputs_dir / "flex.pdbqt"
        output = run_dir / "out.pdbqt"
        log = run_dir / "log.txt"
        config = run_dir / "config_snapshot.txt"
        receptor.write_text(
            _atom(
                100,
                "CA",
                "C",
                0.0,
                0.0,
                0.0,
                residue_name="ALA",
                chain_id="A",
                residue_number=1,
            )
            + "\n",
            encoding="utf-8",
        )
        ligand.write_text(LIGAND_INPUT, encoding="utf-8")
        flex.write_text(FLEX_INPUT, encoding="utf-8")
        output.write_text(VINA_OUTPUT, encoding="utf-8")
        log.write_text(STANDARD_VINA_LOG, encoding="utf-8")
        config.write_text(
            "center_x = 0\ncenter_y = 0\ncenter_z = 0\n"
            "size_x = 20\nsize_y = 20\nsize_z = 20\n",
            encoding="utf-8",
        )

        receptor_rel = f"runs/{run_id}/inputs/receptor.pdbqt"
        ligand_rel = f"runs/{run_id}/inputs/ligand.pdbqt"
        flex_rel = f"runs/{run_id}/inputs/flex.pdbqt"
        output_rel = f"runs/{run_id}/out.pdbqt"
        config_rel = f"runs/{run_id}/config_snapshot.txt"
        protocol_rel = (
            f"runs/{run_id}/inputs/flexible_receptor_protocol.json"
        )
        movement_rel = f"runs/{run_id}/flexible_movement.json"
        contract = {
            "schema_id": "dockstart.flexible_movement.v1",
            "method": (
                "same_receptor_frame_heavy_atom_displacement_no_alignment"
            ),
            "artifact_file": movement_rel,
            "required_after_analysis": True,
            "exclude_flexible_ca_root": True,
        }

        protocol_snapshot: dict[str, object] | None = None
        if modern:
            protocol = inputs_dir / "flexible_receptor_protocol.json"
            protocol.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "protocol_id": "flexible_single",
                        "mode": "flexible",
                        "selected_residues": [
                            {"selector": "A:42", "residue_name": "TYR"}
                        ],
                        "analysis_contracts": {
                            "flexible_movement": contract
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            protocol_snapshot = _snapshot(protocol, protocol_rel)

        metadata: dict[str, object] = {
            "run_id": run_id,
            "status": "finished",
            "run_mode": run_mode,
            "log_file": f"runs/{run_id}/log.txt",
            "output_file": output_rel,
            "config_snapshot": config_rel,
            "config_file": "configs/vina_config.txt",
            "scoring_protocol": "vina",
            "scoring_function": "vina",
            "vina_version": "1.2.7",
            "command": [
                "vina",
                "--config",
                config_rel,
                "--out",
                output_rel,
                "--flex",
                flex_rel,
            ],
            "docking_protocol": {
                "schema_version": 2 if modern else 1,
                "protocol_id": "flexible_single",
                "mode": "flexible",
                "run_mode": run_mode,
                "selected_residues": [
                    {"selector": "A:42", "residue_name": "TYR"}
                ],
            },
            "snapshots": {
                "inputs": {
                    "receptor": _snapshot(receptor, receptor_rel),
                    "ligand": _snapshot(ligand, ligand_rel),
                    "flex": _snapshot(flex, flex_rel),
                },
                "config": _snapshot(config, config_rel),
                "box": {
                    "center_x": 0,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 20,
                    "size_y": 20,
                    "size_z": 20,
                },
                "vina": {
                    "exhaustiveness": 8,
                    "num_modes": 2,
                    "energy_range": 3,
                    "cpu": 1,
                    "seed": 12345,
                    "scoring": "vina",
                },
                **(
                    {"flexible_receptor_protocol": protocol_snapshot}
                    if protocol_snapshot is not None
                    else {}
                ),
            },
            "input_sha256": {
                "receptor": _sha256(receptor),
                "ligand": _sha256(ligand),
                "flex": _sha256(flex),
                "config": _sha256(config),
                **(
                    {
                        "flexible_receptor_protocol": str(
                            protocol_snapshot["sha256"]
                        )
                    }
                    if protocol_snapshot is not None
                    else {}
                ),
            },
            "output_normalization": {
                "normalized_sha256": _sha256(output),
            },
            "artifacts": {
                "out": _snapshot(output, output_rel),
            },
            "artifact_sha256": {
                "out": _sha256(output),
            },
            **(
                {"analysis_contracts": {"flexible_movement": contract}}
                if modern and run_mode == "dock"
                else {}
            ),
        }
        metadata_path = run_dir / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        project_path = project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["runs"] = [
            {
                "run_id": run_id,
                "status": "finished",
                "metadata_file": f"runs/{run_id}/metadata.json",
                "best_affinity": None,
            }
        ]
        project_path.write_text(
            json.dumps(project, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return project_dir, run_id

    def _write_legacy_scores(self, project_dir: Path, run_id: str) -> None:
        scores = (
            "mode,affinity_kcal_mol,rmsd_lb,rmsd_ub\r\n"
            "1,-8.7,0.0,0.0\r\n"
            "2,-8.2,1.532,2.145\r\n"
        )
        run_scores = project_dir / "runs" / run_id / "scores.csv"
        project_scores = project_dir / "results" / "scores.csv"
        run_scores.write_text(scores, encoding="utf-8", newline="")
        project_scores.write_text(scores, encoding="utf-8", newline="")
        metadata_path = project_dir / "runs" / run_id / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.update(
            {
                "scores_file": f"runs/{run_id}/scores.csv",
                "project_scores_file": "results/scores.csv",
                "best_affinity": -8.7,
                "analyzed_at": "2026-07-30T00:00:00+00:00",
            }
        )
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _create_analyzed_modern_run(
        self,
        temp_dir: str,
    ) -> tuple[Path, str, dict[str, object]]:
        project_dir, run_id = self._create_run(
            temp_dir,
            modern=True,
        )
        analyzed = analyze_vina_run_results(str(project_dir), run_id)
        self.assertTrue(analyzed["ok"], analyzed)
        return project_dir, run_id, analyzed

    def test_modern_flexible_analysis_publishes_canonical_evidence_everywhere(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id, analyzed = (
                self._create_analyzed_modern_run(temp_dir)
            )

            movement_path = (
                project_dir
                / "runs"
                / run_id
                / "flexible_movement.json"
            )
            self.assertTrue(movement_path.is_file())
            movement = analyzed["flexible_movement"]
            self.assertEqual(
                movement_path.read_text(encoding="utf-8"),
                canonical_flexible_movement_json(movement),
            )
            self.assertEqual(movement["mode_count"], 2)
            self.assertEqual(
                movement["modes"][0]["ligand"][
                    "heavy_atom_rmsd_no_alignment_angstrom"
                ],
                2.0,
            )
            self.assertEqual(
                movement["modes"][0]["flexible_residues"][0][
                    "residue"
                ]["canonical_id"],
                "A:42",
            )

            metadata = json.loads(
                (
                    project_dir
                    / "runs"
                    / run_id
                    / "metadata.json"
                ).read_text(encoding="utf-8")
            )
            movement_rel = f"runs/{run_id}/flexible_movement.json"
            self.assertEqual(
                metadata["flexible_movement_file"],
                movement_rel,
            )
            self.assertEqual(metadata["flexible_movement"]["mode_count"], 2)
            self.assertEqual(
                metadata["artifacts"]["flexible_movement"]["sha256"],
                _sha256(movement_path),
            )
            self.assertEqual(
                metadata["artifact_sha256"]["flexible_movement"],
                _sha256(movement_path),
            )

            project = json.loads(
                (project_dir / "project.json").read_text(encoding="utf-8")
            )
            run_summary = project["runs"][0]
            self.assertEqual(
                run_summary["flexible_movement_file"],
                movement_rel,
            )
            self.assertEqual(
                run_summary["flexible_movement_mode_count"],
                2,
            )

            loaded = load_scores_csv(str(project_dir), run_id)
            self.assertTrue(loaded["ok"], loaded)
            self.assertTrue(loaded["flexible_movement_available"])
            self.assertFalse(loaded["flexible_movement_legacy_partial"])
            self.assertEqual(loaded["flexible_movement"], movement)

            report = build_markdown_report(str(project_dir), run_id)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["flexible_movement"], movement)
            self.assertEqual(
                report["flexible_movement_file"],
                movement_rel,
            )
            self.assertIn("RMSD l.b./u.b.", report["report_text"])
            self.assertIn("docking score", report["report_text"])

    def test_tampered_movement_artifact_is_rejected_by_load_and_report(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id, _ = self._create_analyzed_modern_run(
                temp_dir
            )
            movement_path = (
                project_dir
                / "runs"
                / run_id
                / "flexible_movement.json"
            )
            payload = json.loads(movement_path.read_text(encoding="utf-8"))
            payload["modes"][0]["ligand"][
                "heavy_atom_rmsd_no_alignment_angstrom"
            ] = 999.0
            movement_path.write_text(
                canonical_flexible_movement_json(payload),
                encoding="utf-8",
            )

            loaded = load_scores_csv(str(project_dir), run_id)
            report = build_markdown_report(str(project_dir), run_id)

            self.assertFalse(loaded["ok"], loaded)
            self.assertEqual(
                loaded["error"]["code"],
                "FLEX_MOVEMENT_ARTIFACT_HASH_MISMATCH",
            )
            self.assertFalse(report["ok"], report)
            self.assertEqual(
                report["error"]["code"],
                "FLEX_MOVEMENT_ARTIFACT_HASH_MISMATCH",
            )

    def test_tampered_frozen_inputs_or_output_are_rejected(self) -> None:
        cases = {
            "ligand": "runs/run_001/inputs/ligand.pdbqt",
            "flex": "runs/run_001/inputs/flex.pdbqt",
            "out": "runs/run_001/out.pdbqt",
        }
        for role, relative_path in cases.items():
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, run_id, _ = (
                    self._create_analyzed_modern_run(temp_dir)
                )
                target = project_dir / relative_path
                target.write_bytes(target.read_bytes() + b"\nREMARK tampered\n")

                loaded = load_scores_csv(str(project_dir), run_id)

                self.assertFalse(loaded["ok"], loaded)
                self.assertTrue(
                    loaded["error"]["code"].startswith("FLEX_MOVEMENT_"),
                    loaded,
                )

    def test_legacy_schema_v1_flexible_run_remains_partial_but_reportable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(
                temp_dir,
                modern=False,
            )
            self._write_legacy_scores(project_dir, run_id)

            loaded = load_scores_csv(str(project_dir), run_id)
            report = build_markdown_report(str(project_dir), run_id)

            self.assertTrue(loaded["ok"], loaded)
            self.assertFalse(loaded["flexible_movement_available"])
            self.assertTrue(loaded["flexible_movement_legacy_partial"])
            self.assertIsNone(loaded["flexible_movement"])
            self.assertTrue(loaded["flexible_movement_warning"])
            self.assertTrue(report["ok"], report)
            self.assertIsNone(report["flexible_movement"])
            self.assertIn(
                loaded["flexible_movement_warning"],
                report["report_text"],
            )

    def test_score_only_and_local_only_do_not_require_movement_artifact(
        self,
    ) -> None:
        for run_mode in ("score_only", "local_only"):
            with self.subTest(run_mode=run_mode), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, run_id = self._create_run(
                    temp_dir,
                    modern=False,
                    run_mode=run_mode,
                )
                self._write_legacy_scores(project_dir, run_id)

                loaded = load_scores_csv(str(project_dir), run_id)
                metadata = loaded.get("metadata") or {}

                self.assertTrue(loaded["ok"], loaded)
                self.assertFalse(
                    project_module._is_flexible_movement_run(metadata)
                )
                self.assertFalse(
                    project_module._movement_artifact_required(metadata)
                )
                self.assertFalse(loaded["flexible_movement_available"])
                self.assertFalse(
                    loaded["flexible_movement_legacy_partial"]
                )

    def test_fixed_artifact_path_and_contract_downgrade_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id, _ = self._create_analyzed_modern_run(
                temp_dir
            )
            metadata_path = (
                project_dir / "runs" / run_id / "metadata.json"
            )
            metadata = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
            metadata["flexible_movement_file"] = (
                "results/flexible_movement.json"
            )
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            wrong_path = load_scores_csv(str(project_dir), run_id)
            self.assertFalse(wrong_path["ok"], wrong_path)
            self.assertEqual(
                wrong_path["error"]["code"],
                "FLEX_MOVEMENT_ARTIFACT_PATH_INVALID",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id, _ = self._create_analyzed_modern_run(
                temp_dir
            )
            metadata_path = (
                project_dir / "runs" / run_id / "metadata.json"
            )
            metadata = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
            metadata["analysis_contracts"]["flexible_movement"][
                "required_after_analysis"
            ] = False
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            downgraded = load_scores_csv(str(project_dir), run_id)
            self.assertFalse(downgraded["ok"], downgraded)
            self.assertEqual(
                downgraded["error"]["code"],
                "FLEX_MOVEMENT_CONTRACT_MISMATCH",
            )


if __name__ == "__main__":
    unittest.main()
