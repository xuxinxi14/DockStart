# 4DM3 simultaneous two-ligand scientific gate

This directory intentionally contains metadata only. It does not redistribute
the 4DM3 coordinate file, ligand SDF files, prepared PDBQT files, executables,
or docking outputs. All upstream coordinates must remain outside the
repository and must match the identities pinned in `source_manifest.json`.

The fixture covers the experimental DockStart simultaneous two-ligand
workflow with the same-chain 4DM3 fragment pair:

- receptor: human phenylethanolamine N-methyltransferase, author chain A;
- retained receptor cofactor: AdoHcy/SAH, author chain A residue 2001;
- docking member 1: resorcinol/RCO, author chain A residue 2002; and
- docking member 2: imidazole/IMD, author chain A residue 2003.

The RCSB entry DOI is `10.2210/pdb4DM3/pdb`. The associated paper is
“Missing Fragments: Detecting Cooperative Binding in Fragment-Based Drug
Design”, DOI `10.1021/ml300015u`. The paper identifies RCO and IMD in the
AdoHcy/SAH-containing complex and discusses their cooperative fragment
context. This gate does **not** claim that a Vina joint score decomposes or
quantifies that cooperativity.

## Pinned external inputs

The required inputs are:

1. the RCSB 4DM3 mmCIF file;
2. the RCSB ModelServer RCO instance SDF;
3. the RCSB ModelServer IMD instance SDF; and
4. the RCSB ModelServer SAH instance SDF used as the explicit receptor
   template.

The legacy RCSB PDB file is pinned only as optional cross-evidence. It is not
an accepted replacement for the mmCIF source of truth.

ModelServer SDF responses contain dynamic job metadata. Their identity is
therefore calculated after converting CRLF and lone CR to LF and retaining
only the first mol block through its `M  END` line. Nothing inside that mol
block is otherwise normalized.

## Structure and preparation contract

Only model 1 is used. The receptor contains author chain A polymer atoms plus
SAH A:2001. RCO A:2002, IMD A:2003, all water, and all of author chain B are
excluded. The pinned atom accounting is 4,394 total mmCIF atom-site records:
2,042 retained receptor atoms and 2,352 excluded atoms.

RCO has two complete crystallographic conformers. Altloc A has occupancy 0.33
and altloc B has occupancy 0.48. The formal docking input uses the
highest-occupancy altloc B. The RCO SDF supplies the pinned topology and
altloc-A coordinates, but it does not contain crystallographic atom names.
Names are assigned by a complete element-and-coordinate match to mmCIF
altloc A; those names then select the corresponding altloc-B coordinates
before ligand preparation. Crystal recovery accepts the minimum
symmetry-aware RMSD against either A or B. Meeko represents the two C-O bonds
as branches, so the prepared RCO PDBQT has two effective search torsions; IMD
has zero.

The explicit RCO reflection swaps C1 with C3, C4 with C6, and O1 with O3,
while C2 and C5 remain fixed. Output atoms must first be traced through
Meeko's index map; output serial order is not an identity oracle.

IMD has one crystallographic conformer. Its RMSD oracle includes the explicit
resonance-equivalent reflection that swaps N1 with N3 and C4 with C5 while C2
remains fixed. This mapping is required in addition to any automorphisms found
from a particular Kekulé representation.

SAH remains part of the rigid receptor, but it is not passed to Meeko as a
receptor residue template. The pinned SAH SDF is hydrogenated and prepared
through the Meeko ligand path. Its prepared atom records are then renumbered,
assigned residue name SAH, and merged with the separately prepared rigid
protein receptor. Preparation is fail-closed: no `allow_bad_res`-style
deletion of unknown or unmatched protein residues is permitted.

The dry-receptor choice excludes all crystallographic water, including the
waters identified around the RCO site in the deposited structure. That is a
frozen modelling assumption for this gate, not a claim that the waters are
scientifically irrelevant. Likewise, the prepared neutral SAH state is a
frozen computational microstate, not an experimentally unique protonation
assignment.

## Docking box

The box is derived from the heavy-atom union of RCO altloc A, RCO altloc B,
and IMD. For each axis:

1. calculate the source minimum and maximum;
2. add 6.0 Å padding on each side;
3. divide by the 0.375 Å spacing and round the interval count upward; and
4. increment an odd interval count to the next even value.

The center remains the midpoint of the unpadded source bounds. The resulting
contract is:

```text
center    = (27.8735, 44.1030, 17.7445)
intervals = (42, 44, 52)
size      = (15.75, 16.50, 19.50) Å
```

## Multi-seed acceptance

The gate uses Vina scoring, a rigid receptor, the receptor-grid workflow,
`exhaustiveness=32`, `num_modes=20`, `energy_range=5`, `cpu=1`, and three
distinct seeds plus an exact repeat of the first seed.

The blocking scientific gates are:

- every distinct seed completes and contains at least one joint pose within
  the 20 returned modes for which both RCO and IMD have no-fit,
  symmetry-aware heavy-atom RMSD at or below 2.0 Å;
- the selected recovered poses have a cross-seed pairwise RMSD diameter at or
  below 2.0 Å for each member;
- the mode-1 joint-affinity range across the three distinct seeds is at most
  1.0 kcal/mol; and
- the repeated seed produces byte-identical raw and normalized PDBQT output,
  identical parsed score rows, and an identical canonical scientific payload.

The selected recovered pose is the passing mode with the smallest quadratic
mean of the RCO and IMD crystal RMSDs; rank is used only as a deterministic
tie-break. This keeps the sampling-convergence test independent of Vina's
ranking order.

Recovery within the top five modes is a non-blocking ranking diagnostic with
a target of at least two of three distinct seeds. It is deliberately not a
DockStart correctness gate: sampling integrity, reproducibility, and honest
reporting are software obligations, whereas the Vina ranking performance of
this experimental protocol is not.

Vina emits one score for each **joint** RCO+IMD pose. A per-member affinity is
unavailable and must never be inferred. Joint scores also must not be compared
with single-ligand scores as though they were the same scientific quantity.

The manifest also records a Windows observation made with the exact pinned
tool binaries. Those ranks, affinities, and output hashes are provenance, not
cross-platform byte or ranking oracles. The acceptance thresholds above remain
the portable contract.

The executable verifier is maintained separately in
`scripts/verify_multiple_ligands_4dm3.py`. It must fetch nothing implicitly,
must use only caller-supplied external sources, and must clean up all temporary
projects and outputs.
