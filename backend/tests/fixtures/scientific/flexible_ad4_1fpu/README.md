# 1FPU flexible AD4 external gate

This directory contains metadata only. It does not contain AutoGrid maps,
AutoGrid4, Vina, or the official 1IEP ligand. The hydrogenated 1FPU receptor
already tracked at `../flexible_1fpu/1fpu_receptorH.pdb` is reused by exact
SHA256.

Run from the repository root with the exact AutoDock Vina v1.2.7
`example/flexible_docking/data/1iep_ligand.pdbqt`, a Python runtime containing
Meeko 0.7.1, and a caller-pinned external AutoGrid4 4.2.6+ executable:

```powershell
python scripts/verify_ad4_flexible_1fpu.py `
  --ligand-pdbqt C:\path\to\1iep_ligand.pdbqt `
  --python C:\path\to\python.exe `
  --vina C:\path\to\vina.exe `
  --autogrid4 C:\path\to\autogrid4.exe `
  --autogrid4-sha256 <64-hex-sha256> `
  --output output\qa\ad4-flexible-1fpu.json
```

The verifier is offline and fail-closed. Missing or mismatched files are not
downloaded or substituted. A passing score and pose-recovery check are
software acceptance evidence only; they do not establish binding or efficacy.
