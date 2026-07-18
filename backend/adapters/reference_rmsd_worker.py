"""RDKit worker for symmetry-aware reference-ligand RMSD calculations.

This file is launched with DockStart's configured/Assisted Python runtime so
the main backend remains independent from RDKit.  It reads the SMILES atom map
written by Meeko into PDBQT files and therefore does not infer bonds from pose
coordinates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _error(code: str, message: str, detail: str = "") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "detail": detail}}


def _pdbqt_pose_lines(text: str, mode: int) -> list[str]:
    models: list[list[str]] = []
    unwrapped_pose: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("MODEL"):
            current = []
            models.append(current)
        elif line.startswith("ENDMDL"):
            current = None
        elif line.startswith(("ATOM  ", "HETATM")):
            if current is not None:
                current.append(line)
            elif not models:
                # A prepared ligand PDBQT contains one pose without MODEL.
                unwrapped_pose.append(line)
    if not models and unwrapped_pose:
        models.append(unwrapped_pose)
    if mode < 1 or mode > len(models):
        raise ValueError(f"请求 Mode {mode}，文件中只有 {len(models)} 个构象。")
    return models[mode - 1]


def _meeko_smiles_map(text: str) -> tuple[str, dict[int, int]]:
    smiles = ""
    mapping: dict[int, int] = {}
    for line in text.splitlines():
        if line.startswith("REMARK SMILES ") and not line.startswith("REMARK SMILES IDX"):
            smiles = line[len("REMARK SMILES ") :].strip()
        elif line.startswith("REMARK SMILES IDX"):
            fields = line[len("REMARK SMILES IDX") :].split()
            if len(fields) % 2:
                raise ValueError("PDBQT 中的 REMARK SMILES IDX 不是成对索引。")
            for index in range(0, len(fields), 2):
                mapping[int(fields[index]) - 1] = int(fields[index + 1])
    if not smiles:
        raise ValueError("PDBQT 缺少 Meeko REMARK SMILES，无法可靠恢复化学键。")
    if not mapping:
        raise ValueError("PDBQT 缺少 Meeko REMARK SMILES IDX，无法可靠映射坐标。")
    return smiles, mapping


def molecule_from_pdbqt(path: Path, mode: int):
    from rdkit import Chem
    from rdkit.Geometry import Point3D

    text = path.read_text(encoding="utf-8", errors="replace")
    smiles, mapping = _meeko_smiles_map(text)
    pose_lines = _pdbqt_pose_lines(text, mode)
    coordinates: dict[int, tuple[float, float, float]] = {}
    for line in pose_lines:
        serial = int(line[6:11])
        coordinates[serial] = (
            float(line[30:38]),
            float(line[38:46]),
            float(line[46:54]),
        )

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError("RDKit 无法解析 PDBQT 中记录的 SMILES。")
    molecule = Chem.RemoveHs(molecule)
    if molecule.GetNumAtoms() != len(mapping):
        raise ValueError(
            f"SMILES 重原子数为 {molecule.GetNumAtoms()}，坐标映射数为 {len(mapping)}，两者不一致。"
        )
    conformer = Chem.Conformer(molecule.GetNumAtoms())
    for atom_index in range(molecule.GetNumAtoms()):
        serial = mapping.get(atom_index)
        if serial is None or serial not in coordinates:
            raise ValueError(f"缺少 SMILES 原子 {atom_index + 1} 对应的 PDBQT 坐标。")
        x, y, z = coordinates[serial]
        conformer.SetAtomPosition(atom_index, Point3D(x, y, z))
    molecule.RemoveAllConformers()
    molecule.AddConformer(conformer, assignId=True)
    return molecule


def load_reference_molecule(path: Path):
    from rdkit import Chem

    suffix = path.suffix.lower()
    molecule = None
    if suffix == ".sdf":
        supplier = Chem.SDMolSupplier(str(path), removeHs=True, sanitize=True)
        molecule = next((item for item in supplier if item is not None), None)
    elif suffix == ".mol":
        molecule = Chem.MolFromMolFile(str(path), removeHs=True, sanitize=True)
    elif suffix == ".pdb":
        molecule = Chem.MolFromPDBFile(str(path), removeHs=True, sanitize=True)
    elif suffix == ".pdbqt":
        molecule = molecule_from_pdbqt(path, 1)
    else:
        raise ValueError("参考配体仅支持 SDF、MOL、PDB 或 PDBQT。")
    if molecule is None:
        raise ValueError("RDKit 未能读取参考配体；请检查文件是否包含有效三维结构和键级。")
    molecule = Chem.RemoveHs(molecule)
    if molecule.GetNumConformers() == 0:
        raise ValueError("参考配体没有三维坐标。")
    return molecule


def calculate(pdbqt_path: Path, reference_path: Path, mode: int) -> dict[str, Any]:
    from rdkit import Chem, rdBase
    from rdkit.Chem import rdMolAlign, rdMolDescriptors

    pose = molecule_from_pdbqt(pdbqt_path, mode)
    reference = load_reference_molecule(reference_path)
    if pose.GetNumAtoms() != reference.GetNumAtoms():
        return _error(
            "REFERENCE_ATOM_COUNT_MISMATCH",
            "参考配体与对接构象的重原子数不同，不能计算共晶 RMSD。",
            f"pose={pose.GetNumAtoms()}; reference={reference.GetNumAtoms()}",
        )
    # Require graph isomorphism before alignment. This avoids silently pairing
    # unrelated atoms merely because their element counts happen to match.
    if not reference.HasSubstructMatch(pose, useChirality=False) or not pose.HasSubstructMatch(
        reference, useChirality=False
    ):
        return _error(
            "REFERENCE_GRAPH_MISMATCH",
            "参考配体与对接构象的化学连接不一致，不能计算共晶 RMSD。",
            "请使用与对接配体相同化学实体的共晶结构，并确认盐和质子化处理。",
        )
    # CalcRMS intentionally does not align the ligand by itself. Docking poses
    # and the co-crystal reference already share the receptor coordinate frame;
    # independently superposing the ligand would hide a misplaced pose.
    rmsd = float(rdMolAlign.CalcRMS(pose, reference, maxMatches=100000))
    return {
        "ok": True,
        "mode": mode,
        "rmsd_angstrom": rmsd,
        "method": "RDKit CalcRMS（同一受体坐标系、重原子、对称性修正）",
        "heavy_atom_count": pose.GetNumAtoms(),
        "pose_formula": rdMolDescriptors.CalcMolFormula(pose),
        "reference_formula": rdMolDescriptors.CalcMolFormula(reference),
        "pose_formal_charge": int(Chem.GetFormalCharge(pose)),
        "reference_formal_charge": int(Chem.GetFormalCharge(reference)),
        "rdkit_version": rdBase.rdkitVersion,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdbqt", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--mode", required=True, type=int)
    args = parser.parse_args()
    try:
        payload = calculate(Path(args.pdbqt), Path(args.reference), args.mode)
    except ModuleNotFoundError as exc:
        code = "REFERENCE_RMSD_RDKIT_UNAVAILABLE" if (exc.name or "").startswith("rdkit") else "REFERENCE_RMSD_FAILED"
        payload = _error(code, "当前 Python 环境没有可用的 RDKit，无法计算对称性修正 RMSD。", str(exc))
    except Exception as exc:  # noqa: BLE001 - worker always returns structured JSON.
        payload = _error("REFERENCE_RMSD_FAILED", "共晶参考 RMSD 计算失败。", str(exc))
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
