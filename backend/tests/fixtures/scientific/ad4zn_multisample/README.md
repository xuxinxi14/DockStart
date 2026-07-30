# AD4Zn 2OI0 / 1R1J external acceptance fixture

This directory is metadata only. It contains no RCSB structure, ligand,
AD4Zn parameter file, prepared receptor, map, docking output, executable, or
other third-party scientific asset.

The verifier intentionally does not download anything. The caller must supply
all nine paths explicitly:

- official RCSB `2OI0.pdb`;
- official RCSB `1R1J.pdb`;
- the RCSB ModelServer ligand-instance SDF for `2OI0 / 283 / A:1`;
- the RCSB ModelServer ligand-instance SDF for `1R1J / OIR / A:2001`;
- the `mk_prepare_receptor` launcher;
- a Python 3.11.15 runtime containing Meeko 0.7.1 and RDKit 2026.03.3;
- external AutoGrid 4.2.7 or newer;
- AutoDock Vina 1.2.7 with `--maps` support; and
- the repository-level AutoDock Vina v1.2.7 `data/AD4Zn.dat`.

Run from the DockStart repository root:

```powershell
python scripts/verify_ad4zn_multisample.py `
  --pdb-2oi0 C:\scientific-inputs\2OI0.pdb `
  --pdb-1r1j C:\scientific-inputs\1R1J.pdb `
  --ligand-2oi0-sdf C:\scientific-inputs\2OI0_283_A1_instance.sdf `
  --ligand-1r1j-sdf C:\scientific-inputs\1R1J_OIR_A2001_instance.sdf `
  --mk-prepare-receptor C:\toolchain\Scripts\mk_prepare_receptor.exe `
  --python C:\toolchain\python.exe `
  --autogrid C:\ADFRsuite\bin\autogrid4.exe `
  --vina C:\vina\vina.exe `
  --ad4zn-dat C:\AutoDock-Vina-v1.2.7\data\AD4Zn.dat
```

All source text files must match the pinned LF-normalized SHA-256 identity.
Consistent LF and CRLF forms are accepted; mixed line endings or any other
content change are rejected before preparation starts. On Windows, the
validated executable SHA-256 values are also mandatory. On another platform,
the exact supplied paths, versions, command-line identities, and capabilities
remain mandatory and each binary SHA-256 is recorded.

## Dataset-specific receptor preparation

The verifier does not claim that an arbitrary raw PDB can be prepared
automatically. It invokes the caller-supplied Meeko launcher with a pinned
deletion contract for each reviewed dataset:

- 2OI0: delete bound ligand `A:1` and waters `A:482-622`; retain `ZN A:2`.
- 1R1J: delete incomplete remote residue `A:505`, NAG `A:752-754`,
  bound ligand OIR `A:2001`, and waters `A:2002-2089`; retain `ZN A:1001`.

For 1R1J, the verifier independently calculates the shortest raw-coordinate
distance from residue A:505 to Zn and requires it to be greater than 35 Å.
This documents why the Meeko-specific deletion is remote from the reviewed
metal site; it does not reinterpret or repair that residue.

## Acceptance chain

For each system the gate exercises:

1. canonical identity and raw PDB structural checks;
2. raw PDB to receptor PDBQT with the explicit deletion contract;
3. DockStart's public raw-ligand import and RDKit/Meeko preparation API;
4. exactly one Zn, one supported three-coordinate site, and one zero-charge TZ;
5. requested Box and rounded AutoGrid effective-grid coverage of both ZN/TZ;
6. external AutoGrid map generation and frozen map evidence;
7. two Vina AD4 runs using the same fixed seed;
8. byte-identical repeated output, score/mode gates, and Markdown report
   evidence; and
9. a diagnostic direct heavy-atom RMSD from the frozen prepared ligand PDBQT
   to each output mode, calculated in unchanged PDBQT heavy-atom order.

The RMSD diagnostic does not align structures and does not apply symmetry
correction. Its threshold is deliberately loose. The verifier never requires
the best-scoring mode to be the mode with the lowest diagnostic RMSD.

Exact generated SHA-256 values are evidence only for the pinned Windows
toolchain. The fixed output identity is the DockStart-normalized Vina output
from the exact public ligand-preparation artifact, including its pinned
Windows line-ending identity. Cross-platform scientific acceptance relies on
source identity, structure contracts, Box/grid coverage, map completeness,
repeatability within that toolchain, score windows, mode counts, the loose
RMSD diagnostic, and report evidence.

This gate broadens real-system coverage beyond 1S63, but it is not evidence
that every zinc protein or coordination geometry is supported.
