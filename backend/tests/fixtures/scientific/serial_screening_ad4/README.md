# Serial AD4 screening reliability gate

This metadata-only gate derives twelve unique audit files from the exact 5X72
P59/P69 PDBQT bytes by prepending one deterministic `REMARK` line. The workload
is designed to test queue reliability, not chemical diversity.

```powershell
python scripts/verify_ad4_serial_screening.py `
  --receptor-pdbqt C:\path\to\5x72_receptor.pdbqt `
  --ligand-p59-pdbqt C:\path\to\5x72_ligand_p59.pdbqt `
  --ligand-p69-pdbqt C:\path\to\5x72_ligand_p69.pdbqt `
  --vina C:\path\to\vina.exe `
  --autogrid4 C:\path\to\autogrid4.exe `
  --autogrid4-sha256 <64-hex-sha256> `
  --output output\qa\ad4-serial-screening.json
```

The gate proves one frozen map set, live cancellation, tamper-blocked resume,
explicit recovery, continuation after one injected failure, ranking, report,
archive, and ZIP export. DockStart still launches independent Vina runs; it
does not jointly score the library.
