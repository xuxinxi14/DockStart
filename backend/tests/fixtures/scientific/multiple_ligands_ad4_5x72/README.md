# 5X72 simultaneous two-ligand AD4 external gate

This directory contains metadata only. Supply the exact receptor, P59, and P69
PDBQT files from the AutoDock Vina v1.2.7
`example/mulitple_ligands_docking/solution` directory. No upstream maps or
executables are bundled here.

```powershell
python scripts/verify_ad4_multiple_ligands_5x72.py `
  --receptor-pdbqt C:\path\to\5x72_receptor.pdbqt `
  --ligand-p59-pdbqt C:\path\to\5x72_ligand_p59.pdbqt `
  --ligand-p69-pdbqt C:\path\to\5x72_ligand_p69.pdbqt `
  --vina C:\path\to\vina.exe `
  --autogrid4 C:\path\to\autogrid4.exe `
  --autogrid4-sha256 <64-hex-sha256> `
  --output output\qa\ad4-multiple-ligands-5x72.json
```

The gate requires one `--ligand` option followed by P59 and P69 in that order,
one frozen union map set, and joint-only scores. It rejects any per-member
affinity interpretation.
