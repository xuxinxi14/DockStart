# Hydrated docking 1UW6 project acceptance gate

This directory contains metadata only. It deliberately contains no protein,
ligand, map, docking-output, executable, or other third-party file.

`scripts/verify_hydrated_1uw6.py` is a project-level acceptance gate. It uses
the same public DockStart project APIs as the GUI instead of calling the
scientific primitives as an isolated test. The caller must supply both:

- a local checkout of the official AutoDock Vina repository at tag `v1.2.7`,
  commit `8eb40404f4f45608acb3b01427587ac049f27c1f`; and
- an external AutoGrid executable at version 4.2.6 or newer.

The gate never clones a repository, downloads a tool, or copies third-party
reference assets into DockStart. AutoGrid must be supplied explicitly with
`--autogrid`; a bundled or silently discovered executable is rejected.

## Reference identity

The upstream example is `example/hydrated_docking` from the Apache-2.0
AutoDock Vina repository. `source_manifest.json` records two identities for
each required text file:

- the known Windows CRLF byte size and SHA-256, retained as provenance; and
- the required portable identity, calculated after converting CRLF and lone
  CR line endings to LF and then hashing the resulting bytes.

Each record also pins its Git blob. A checkout with LF or CRLF line endings is
accepted only when it is the exact recorded LF or CRLF byte form and its
canonical LF content matches. Mixed line endings are rejected. No whitespace,
number, atom, map-value, or other content normalization is performed. When
the supplied directory is a Git worktree, it must be the exact worktree root;
the commit, tag, and every required blob must match. Without Git metadata, the
two exact checkout identities remain mandatory.

The crystal-pose reference is the upstream
`example/hydrated_docking/data/1uw6_ligand.sdf` file. Its Git blob, Windows
CRLF bytes, and portable LF content are pinned in the manifest, but the SDF
itself is not copied into this repository. The verifier additionally freezes
its 12-element sequence, 13-bond V2000 topology, and ordered-coordinate
fingerprint. Changing any reference coordinate fails closed even if a caller
tries to supply a different structure under the same filename.

## Run the gate

From the DockStart repository root:

```powershell
python scripts/verify_hydrated_1uw6.py `
  --upstream-root C:\path\to\AutoDock-Vina-v1.2.7 `
  --autogrid C:\path\to\autogrid4.exe
```

`--python` and `--vina` can override the default
`resources/python/python.exe` and `resources/vina/vina.exe`. The selected
Python must provide Meeko 0.7.1. Vina must be 1.2.7 and advertise safe
`--maps` support. The verifier isolates bundled-resource discovery and rejects
any Meeko/Python, AutoGrid, or Vina detection or project subprocess whose
normalized executable path differs from the caller-supplied path.

The command prints one structured JSON result and exits nonzero on failure.
Every completed or failed stage is recorded. The temporary settings file,
project, generated maps, run outputs, scores, and report are removed in a
`finally` cleanup; the JSON result includes the temporary path and whether
removal succeeded.

## Public API chain under test

The gate verifies this complete project workflow:

1. create a temporary DockStart project;
2. import the official raw SDF ligand and receptor PDBQT;
3. save the pinned box and Vina parameters;
4. prepare the hydrated ligand with Meeko and reproduce the official two-W
   PDBQT content byte-for-byte after portable line-ending normalization;
5. generate the GPF, run the supplied external AutoGrid, and publish the base
   AD4 maps;
6. generate the BEST W map and require exact geometry plus numeric value
   identity with the official W map;
7. prepare and run Vina through the generic project run API with `--maps` and
   `--scoring ad4`;
8. preserve the raw Windows Vina output, remove only recognized
   `TORSDOF`/`ENDMDL` NUL padding, and require a UTF-8, control-free normalized
   PDBQT;
9. run hydrated water postprocessing, publish `scores.csv`, and export the
   hydrated Markdown report; and
10. calculate a no-fit heavy-atom RMSD from every pose in the current
    DockStart project run to the pinned co-crystal ligand in the same receptor
    coordinate frame; and
11. compare the official raw output with DockStart's retained-water semantics
    and the read-only upstream `dry.py` reference.

The requested `num_modes` remains 9. This pinned fixed-seed acceptance record
accepts exactly 8 or 9 continuous `MODEL` records; 7 or fewer and 10 or more
are rejected. Every accepted raw pose must retain both W pseudo-atoms, and the
best-affinity neighborhood remains a hard gate.

## Positive pose-recovery oracle

The co-crystal SDF is neutral nicotine, while the hydrated PDBQT carries the
protonated stereochemical SMILES
`C[N@@H+]1CCC[C@H]1c1cccnc1`. Atom correspondence is therefore not guessed
from nearest coordinates or element labels. The manifest freezes all three
parts of the correspondence:

- the exact PDBQT `REMARK SMILES`;
- the exact one-based `REMARK SMILES IDX` pairs; and
- the explicit zero-based mapping from those SMILES atoms to the 12 SDF
  atoms.

Every mapped atom must preserve its element. RMSD is then calculated directly
in the shared receptor coordinate frame over the 12 ligand heavy atoms. No
rotation, translation, Kabsch fit, symmetry search, or independent alignment
is applied. The gate requires the minimum RMSD among Modes 1-3 to be at most
2.0 Å and reports the RMSD of every actual output mode, the best mode, the
threshold, and the pass/fail result. It always evaluates the PDBQT produced by
the current DockStart project API run; the upstream precomputed output is not
substituted as the success result.

This is a minimum positive structural oracle for the single pinned 1UW6
workflow. Passing it demonstrates that this exact hydrated AD4 chain can
recover one crystal-like nicotine pose under the frozen inputs and parameters.
It does not establish cross-target accuracy, broad hydrated-docking
generalization, or production maturity.

## Postprocessing oracle

The upstream `dry.py` byte output is not used as an output oracle. Its topology
handling is unsuitable for a reusable DockStart result file; the clean-room
implementation preserves the original model topology.

The two classification totals intentionally differ. In the v1.2.7 script,
overlap filtering looks for an atom named exactly `W`, while the 1UW6 dummy
waters are named `WAT` and carry `W` only in the final PDBQT type column. The
script also shifts map lookup coordinates by half a grid spacing by deriving
its origin from `NELEMENTS + 1`. DockStart fixes both behaviors by default; the
legacy calculation is retained only as explicit upstream evidence, not as the
runtime scientific rule.
