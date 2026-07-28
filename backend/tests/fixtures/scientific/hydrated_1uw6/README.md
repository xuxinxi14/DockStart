# Hydrated docking 1UW6 external acceptance fixture

This directory contains metadata only. It deliberately does **not** contain
protein, ligand, map, or docking-output files from AutoDock Vina.

The acceptance gate reads those files from a user-supplied checkout of the
official AutoDock Vina repository:

- repository: `https://github.com/ccsb-scripps/AutoDock-Vina`
- tag: `v1.2.7`
- commit: `8eb40404f4f45608acb3b01427587ac049f27c1f`
- example: `example/hydrated_docking`
- upstream license: Apache-2.0

On Windows, create the reference checkout with CRLF conversion enabled so the
byte hashes match `source_manifest.json`:

```powershell
git -c core.autocrlf=true clone --depth 1 --branch v1.2.7 `
  https://github.com/ccsb-scripps/AutoDock-Vina.git AutoDock-Vina-v1.2.7
```

Run the external acceptance gate from the DockStart repository root:

```powershell
python scripts/verify_hydrated_1uw6.py `
  --upstream-root C:\path\to\AutoDock-Vina-v1.2.7
```

`--python` and `--vina` can override the default
`resources/python/python.exe` and `resources/vina/vina.exe`. The verifier does
not download anything and writes all generated files to an automatically
removed temporary directory.

The gate verifies four independent parts of the protocol:

1. Meeko 0.7.1 hydrated ligand preparation adds the two official W pseudo
   atoms and reproduces the recorded PDBQT bytes.
2. DockStart's clean-room BEST water-map implementation reproduces the
   recorded W map.
3. AutoDock Vina reads the AD4 maps, retains the W atoms in the raw poses, and
   returns the official best-affinity neighborhood with a fixed seed. The
   requested `num_modes` is treated as Vina's documented maximum; fewer modes
   are valid when clustering or `energy_range` filters the result.
4. DockStart's clean-room postprocessor classifies all 18 official W atoms,
   removes real ligand/receptor overlaps by final PDBQT type, preserves the
   raw Vina affinities, and emits retained-water plus water-free derivatives.
   A separate read-only compatibility calculation also reproduces the
   documented `dry.py` reference of 7 strong, 4 weak, and 7 displaced waters.

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
