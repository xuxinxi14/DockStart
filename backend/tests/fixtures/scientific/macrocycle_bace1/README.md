# BACE_1 reviewed-macrocycle acceptance fixture

This directory contains the four inputs recorded by the AutoDock Vina v1.2.7
`example/docking_with_macrocycles` example. `fixture_manifest.json` pins every
file by byte size and SHA-256 and freezes the DockStart/Meeko 0.7.1 review
contract currently produced from `BACE_1_ligand.sdf`.

The independent acceptance entry point is:

```powershell
resources\python\python.exe scripts\verify_macrocycle_bace1.py `
  --python resources\python\python.exe `
  --vina resources\vina\vina.exe `
  --output C:\path\to\new-evidence.json
```

The output path is optional and must not already exist. The command never
reuses a historical DockStart project or temporary output. It creates a fresh
project and verifies the following public-API chain:

1. exact fixture identities, configured Python/RDKit/Meeko versions, and the
   fixed Vina 1.2.7 executable size/SHA-256;
2. standard ligand preparation refuses the macrocycle with
   `MACROCYCLE_REVIEW_REQUIRED` and publishes no prepared ligand;
3. a fresh review reproduces all seven candidate identifiers and the fixed
   recommendation;
4. the recommended candidate is confirmed by its immutable record SHA-256;
5. real reviewed Meeko preparation breaks exactly bond `[2, 3]`, emits two
   `G*` glue pseudo-atoms, and cross-binds the review, confirmation, contract,
   frozen input, worker evidence, and published PDBQT;
6. real receptor preparation, the official box, and the full acceptance
   parameters feed a real Vina run whose command, PID identity, execution-time
   Vina hashes, and frozen receptor/ligand snapshots are cross-checked;
7. run metadata attributes the ligand preparation as `formal_reviewed`, and
   DockStart emits scores plus a Markdown report;
8. the real `mk_export` path writes `poses.sdf`; and
9. a separate RDKit process strictly re-reads every exported pose and requires
   no atomic-number-zero or `G*` atoms, 38 heavy atoms, 39 heavy-atom bonds,
   total formal charge zero, and the pinned canonical-isomeric-SMILES hash.

The temporary project is deleted in `finally`; the structured JSON retains
input, tool, evidence, output, command-record, report, and export hashes.

## Scientific scope

Passing this verifier establishes that the pinned BACE_1 workflow can rebuild
the reviewed macrocycle preparation and recover the source heavy-atom
topology and total formal charge after docking export. It does **not** claim
scientific pose recovery. The fixture does not contain an independently
validated same-coordinate-frame crystal-pose atom mapping, so the verifier
does not apply an alignment, invent a reference mapping, or gate on RMSD.
