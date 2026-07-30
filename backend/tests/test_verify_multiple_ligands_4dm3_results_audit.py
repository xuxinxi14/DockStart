from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "verify_multiple_ligands_4dm3.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_multiple_ligands_4dm3_results_audit",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atom_line(
    serial: int,
    name: str,
    atom_type: str,
    x: float,
) -> str:
    return (
        f"ATOM  {serial:5d}  {name:<3} LIG A   1    "
        f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}"
        f"  1.00  0.00     0.000 {atom_type}\n"
    )


def _member_block(name: str, atom_type: str, x: float) -> str:
    return (
        "ROOT\n"
        + _atom_line(1, name, atom_type, x)
        + "ENDROOT\n"
        + "TORSDOF 0\n"
    )


def _output_text(member_1_x: float = 1.0) -> str:
    return (
        "MODEL 1\n"
        "REMARK VINA RESULT: -7.000 0.000 0.000\n"
        + _member_block("C1", "C", member_1_x)
        + _member_block("O1", "OA", 2.0)
        + "ENDMDL\n"
    )


def _log_text() -> str:
    return (
        "AutoDock Vina v1.2.7\n"
        "mode |   affinity | dist from best mode\n"
        "     | (kcal/mol) | rmsd l.b.| rmsd u.b.\n"
        "-----+------------+----------+----------\n"
        "   1       -7.000      0.000      0.000\n"
    )


def _report_text(run_id: str = "run_001") -> str:
    return (
        "# 多配体共同对接（实验性）报告\n"
        "\n"
        f"- Run：`{run_id}`\n"
        "\n"
        "## 协议边界\n"
        "\n"
        "本报告来自一次 Vina 搜索中的两个配体共同优化，不是串行批量筛选。\n"
        "本协议的 affinity 与 RMSD 均属于两个配体组成的联合构象；"
        "DockStart 不提供单个配体的独立评分贡献。\n"
        "当前实验版本仅支持两个唯一 PDBQT 配体、刚性受体、"
        "Vina/Vinardo 评分和全局搜索。\n"
        "\n"
        "## 联合评分\n"
        "\n"
        "| Mode | 联合 affinity (kcal/mol) | RMSD l.b. | RMSD u.b. | 构象可加载 |\n"
        "|---:|---:|---:|---:|---|\n"
        "| 1 | -7.0 | 0.0 | 0.0 | 是 |\n"
        "\n"
        "RMSD 表示整个两配体联合构象相对最佳联合模式的差异，"
        "不是任一成员的单独 RMSD。\n"
        "\n"
        "## 科学说明\n"
        "\n"
        "Docking score 仅供结构结合趋势参考，不能替代实验验证。\n"
        "共同对接结果不能直接证明协同结合、同时占位、药效、"
        "安全性或临床价值。\n"
    )


def _provisional_members() -> list[dict[str, Any]]:
    return [
        {
            "member_index": 1,
            "atom_count": 1,
            "heavy_atom_count": 1,
            "branch_count": 0,
            "effective_branch_count": 0,
            "degenerate_branch_count": 0,
            "torsdof": 0,
        },
        {
            "member_index": 2,
            "atom_count": 1,
            "heavy_atom_count": 1,
            "branch_count": 0,
            "effective_branch_count": 0,
            "degenerate_branch_count": 0,
            "torsdof": 0,
        },
    ]


def _write_snapshot(
    root: Path,
    relative_path: str,
    payload: bytes,
) -> dict[str, Any]:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "relative_path": relative_path,
        "size_bytes": len(payload),
        "sha256": _sha256(payload),
    }


def _build_completed_run(
    root: Path,
    *,
    member_1_x: float = 1.0,
    csv_affinity: float = -7.0,
    tamper_member_pose: bool = False,
    stdout_matches_log: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_id = "run_001"
    prefix = f"runs/{run_id}"
    output = _output_text(member_1_x)
    provisional = _provisional_members()
    first_parse = VERIFY.parse_multiple_ligand_output_text(
        output,
        provisional,
    )
    assert first_parse["ok"], first_parse
    frozen_members = []
    for member_index, parsed_member in enumerate(
        first_parse["models"][0]["members"],
        start=1,
    ):
        stats = {
            key: parsed_member[key]
            for key in (
                "identity_sha256",
                "atom_count",
                "heavy_atom_count",
                "branch_count",
                "effective_branch_count",
                "degenerate_branch_count",
                "torsdof",
            )
        }
        frozen_members.append(
            {
                "member_index": member_index,
                "member_id": f"member_{member_index:03d}",
                "stats": stats,
            }
        )
    parsed = VERIFY.parse_multiple_ligand_output_text(
        output,
        frozen_members,
    )
    assert parsed["ok"], parsed
    parsed_model = parsed["models"][0]
    raw_members = []
    for member in parsed_model["members"]:
        member_index = int(member["member_index"])
        payload = str(member["content"]).encode("utf-8")
        if tamper_member_pose and member_index == 1:
            payload = payload.replace(b"   1.000", b"   1.100", 1)
        relative = (
            f"{prefix}/poses/mode_001/"
            f"member_{member_index:03d}.pdbqt"
        )
        snapshot = _write_snapshot(root, relative, payload)
        raw_members.append(
            {
                "member_index": member_index,
                "member_id": f"member_{member_index:03d}",
                "file": relative,
                "size_bytes": snapshot["size_bytes"],
                "sha256": snapshot["sha256"],
                "atom_count": member["atom_count"],
                "torsdof": member["torsdof"],
                "identity_sha256": member["identity_sha256"],
            }
        )
    api_scores = [
        {
            "mode": 1,
            "joint_affinity_kcal_mol": -7.0,
            "rmsd_lb": 0.0,
            "rmsd_ub": 0.0,
            "pose_available": True,
            "score_is_joint": True,
            "per_member_scores_available": False,
        }
    ]
    joint = {
        "run_id": run_id,
        "protocol_id": "simultaneous_multi_ligand",
        "member_count": 2,
        "members": frozen_members,
        "models": [
            {
                "mode": 1,
                "joint_affinity_kcal_mol": -7.0,
                "rmsd_lb": 0.0,
                "rmsd_ub": 0.0,
                "members": raw_members,
            }
        ],
        "available_modes": [1],
        "scores": api_scores,
        "affinity_scope": "joint_two_ligand_pose",
        "rmsd_scope": "joint_pose_relative_to_best_mode",
        "per_member_scores_available": False,
        "output_model_is_pose_authority": True,
    }
    scores_csv = (
        "mode,joint_affinity_kcal_mol,rmsd_lb,rmsd_ub,"
        "pose_available,score_scope\n"
        f"1,{csv_affinity},0.0,0.0,true,joint_two_ligand_pose\n"
    ).encode("utf-8")
    log_payload = _log_text().encode("utf-8")
    stdout_payload = (
        log_payload if stdout_matches_log else b"different stdout\n"
    )
    artifacts = {
        "output": _write_snapshot(
            root,
            f"{prefix}/out.pdbqt",
            output.encode("utf-8"),
        ),
        "scores": _write_snapshot(
            root,
            f"{prefix}/scores.csv",
            scores_csv,
        ),
        "joint_poses": _write_snapshot(
            root,
            f"{prefix}/joint_poses.json",
            (json.dumps(joint, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            ),
        ),
        "report": _write_snapshot(
            root,
            f"{prefix}/multi_ligand_report.md",
            _report_text(run_id).encode("utf-8"),
        ),
        "stdout": _write_snapshot(
            root,
            f"{prefix}/stdout.txt",
            stdout_payload,
        ),
        "stderr": _write_snapshot(
            root,
            f"{prefix}/stderr.txt",
            b"",
        ),
        "log": _write_snapshot(
            root,
            f"{prefix}/log.txt",
            log_payload,
        ),
    }
    metadata = {
        "members": frozen_members,
        "scores": api_scores,
        "box": {
            "center_x": 0.0,
            "center_y": 0.0,
            "center_z": 0.0,
            "size_x": 20.0,
            "size_y": 20.0,
            "size_z": 20.0,
        },
        "output_file": f"{prefix}/out.pdbqt",
        "scores_file": f"{prefix}/scores.csv",
        "joint_poses_file": f"{prefix}/joint_poses.json",
        "report_file": f"{prefix}/multi_ligand_report.md",
        "stdout_file": f"{prefix}/stdout.txt",
        "stderr_file": f"{prefix}/stderr.txt",
        "log_file": f"{prefix}/log.txt",
        "artifacts": artifacts,
    }
    expected_box = {
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "effective_size": {"x": 20.0, "y": 20.0, "z": 20.0},
    }
    return metadata, expected_box


class MultipleLigand4dm3ResultAuditTests(unittest.TestCase):
    def _audit(
        self,
        root: Path,
        metadata: dict[str, Any],
        expected_box: dict[str, Any],
    ) -> dict[str, Any]:
        return VERIFY._audit_completed_result_provenance(
            root,
            "run_001",
            metadata,
            frozen_members=metadata["members"],
            api_scores=metadata["scores"],
            expected_box=expected_box,
            frozen_num_modes=20,
        )

    def test_completed_result_bytes_form_one_provenance_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata, expected_box = _build_completed_run(root)
            result = self._audit(root, metadata, expected_box)
        self.assertEqual(result["parsed_output"]["available_modes"], [1])
        self.assertEqual(len(result["member_pose_artifacts"]), 2)
        self.assertEqual(result["artifacts"]["stderr"]["size_bytes"], 0)
        self.assertTrue(
            result["box_coverage"][
                "all_output_movable_heavy_atoms_inside_effective_grid"
            ]
        )
        self.assertEqual(
            result["report_semantics"]["reported_joint_score_row_count"],
            1,
        )

    def test_member_pose_must_equal_reparsed_output_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata, expected_box = _build_completed_run(
                root,
                tamper_member_pose=True,
            )
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                self._audit(root, metadata, expected_box)
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_OUTPUT_POSE_PROVENANCE_MISMATCH",
        )

    def test_independent_box_gate_rejects_outside_heavy_atom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata, expected_box = _build_completed_run(
                root,
                member_1_x=10.001,
            )
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                self._audit(root, metadata, expected_box)
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_OUTPUT_GRID_GATE_FAILED",
        )

    def test_scores_csv_values_must_match_log_and_api(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata, expected_box = _build_completed_run(
                root,
                csv_affinity=-6.0,
            )
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                self._audit(root, metadata, expected_box)
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_SCORE_ARTIFACT_MISMATCH",
        )

    def test_stdout_and_log_must_be_exactly_equal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata, expected_box = _build_completed_run(
                root,
                stdout_matches_log=False,
            )
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                self._audit(root, metadata, expected_box)
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_STDOUT_LOG_MISMATCH",
        )

    def test_scores_csv_header_is_exact(self) -> None:
        broken = (
            b"mode,affinity,rmsd_lb,rmsd_ub,pose_available,score_scope\n"
            b"1,-7.0,0.0,0.0,true,joint_two_ligand_pose\n"
        )
        with self.assertRaises(
            VERIFY.MultipleLigand4dm3AcceptanceError
        ) as raised:
            VERIFY._parse_scores_csv_bytes(broken)
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_SCORES_CSV_INVALID",
        )

    def test_report_cannot_expose_member_affinity(self) -> None:
        payload = (
            _report_text()
            + "\n## Member affinity\n\n| 成员 | affinity |\n"
        ).encode("utf-8")
        with self.assertRaises(
            VERIFY.MultipleLigand4dm3AcceptanceError
        ) as raised:
            VERIFY._audit_report_bytes(
                payload,
                scores=[
                    {
                        "mode": 1,
                        "joint_affinity_kcal_mol": -7.0,
                        "rmsd_lb": 0.0,
                        "rmsd_ub": 0.0,
                        "pose_available": True,
                    }
                ],
                expected_run_id="run_001",
            )
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
        )

    def test_final_local_reaudit_rejects_late_byte_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prepared.pdbqt"
            path.write_bytes(b"prepared-v1\n")
            record = {
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path.read_bytes()),
            }
            evidence = VERIFY._read_attested_local_file(
                path,
                record,
                label="prepared test",
            )
            self.assertEqual(evidence["sha256"], record["sha256"])
            path.write_bytes(b"prepared-v2\n")
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                VERIFY._read_attested_local_file(
                    path,
                    record,
                    label="prepared test",
                )
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
        )

    def test_final_reaudit_rechecks_all_run_pose_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata, expected_box = _build_completed_run(root)
            provenance = self._audit(root, metadata, expected_box)

            def local_file(
                relative_path: str,
                payload: bytes,
            ) -> tuple[Path, dict[str, Any]]:
                snapshot = _write_snapshot(root, relative_path, payload)
                return root / relative_path, snapshot

            verified_paths: dict[str, Path] = {}
            source_files: dict[str, Any] = {}
            for key in VERIFY.SOURCE_KEYS:
                path, snapshot = local_file(
                    f"verified/{key}.dat",
                    f"{key}\n".encode("utf-8"),
                )
                verified_paths[key] = path
                source_files[key] = {
                    "content_identity": {
                        "size_bytes": snapshot["size_bytes"],
                        "sha256": snapshot["sha256"],
                    }
                }
            python_path, python_record = local_file(
                "tools/python.exe",
                b"python-binary\n",
            )
            vina_path, vina_record = local_file(
                "tools/vina.exe",
                b"vina-binary\n",
            )
            protein_pdb, protein_record = local_file(
                "prepared/protein.pdb",
                b"protein-pdb\n",
            )
            receptor, receptor_record = local_file(
                "prepared/receptor.pdbqt",
                b"receptor-pdbqt\n",
            )
            prepared_paths: dict[str, Path] = {}
            ligand_evidence: dict[str, Any] = {}
            for label in ("SAH", "RCO", "IMD"):
                path, snapshot = local_file(
                    f"prepared/{label}.pdbqt",
                    f"{label}-pdbqt\n".encode("utf-8"),
                )
                prepared_paths[label] = path
                ligand_evidence[label] = {"pdbqt": snapshot}

            prefix = "runs/run_001"
            receptor_input = _write_snapshot(
                root,
                f"{prefix}/inputs/receptor.pdbqt",
                b"frozen-receptor\n",
            )
            config_input = _write_snapshot(
                root,
                f"{prefix}/config_snapshot.txt",
                b"frozen-config\n",
            )
            member_inputs = []
            for index in (1, 2):
                snapshot = _write_snapshot(
                    root,
                    f"{prefix}/inputs/ligand_{index:03d}.pdbqt",
                    f"member-{index}\n".encode("utf-8"),
                )
                member_inputs.append(
                    {"member_index": index, **snapshot}
                )
            run = {
                "run_id": "run_001",
                "raw_output": {"archived": False},
                "project_api_evidence": {
                    "input_snapshots": {
                        "receptor": receptor_input,
                        "config": config_input,
                        "members": member_inputs,
                    },
                    "completed_result_provenance": {
                        "artifacts": provenance["artifacts"],
                        "member_pose_artifacts": provenance[
                            "member_pose_artifacts"
                        ],
                    },
                },
            }
            evidence = VERIFY._reaudit_final_immutable_state(
                project_root=root,
                manifest={"source_files": source_files},
                verified_paths=verified_paths,
                toolchain_evidence={
                    "python": {"path": str(python_path), **python_record},
                    "vina": {"path": str(vina_path), **vina_record},
                },
                structure_evidence={"protein_pdb": protein_record},
                ligand_preparation_evidence=ligand_evidence,
                receptor_preparation_evidence={
                    "combined_receptor": receptor_record
                },
                prepared_paths=prepared_paths,
                protein_pdb=protein_pdb,
                receptor_pdbqt=receptor,
                runs=[run],
            )
            self.assertEqual(evidence["run_count"], 1)
            pose_path = (
                root
                / "runs/run_001/poses/mode_001/member_001.pdbqt"
            )
            pose_path.write_bytes(
                pose_path.read_bytes().replace(b"   1.000", b"   1.100", 1)
            )
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                VERIFY._reaudit_final_immutable_state(
                    project_root=root,
                    manifest={"source_files": source_files},
                    verified_paths=verified_paths,
                    toolchain_evidence={
                        "python": {
                            "path": str(python_path),
                            **python_record,
                        },
                        "vina": {"path": str(vina_path), **vina_record},
                    },
                    structure_evidence={
                        "protein_pdb": protein_record
                    },
                    ligand_preparation_evidence=ligand_evidence,
                    receptor_preparation_evidence={
                        "combined_receptor": receptor_record
                    },
                    prepared_paths=prepared_paths,
                    protein_pdb=protein_pdb,
                    receptor_pdbqt=receptor,
                    runs=[run],
                )
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_HASH_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
