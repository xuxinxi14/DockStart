# Flexible mmCIF identity fixture

`minimal_identity.cif` is a minimal, syntactically valid PDBx/mmCIF coordinate
fixture constructed for DockStart's residue-identity audit. It is not an
experimental structure and must not be used as scientific docking input.

The fixture deliberately contains:

- label chain `AA` mapped to author chain `A`;
- label sequence IDs `1` and `2` mapped to author residue number `10`;
- insertion code `A` on the second author residue;
- altloc `A` / `B` with occupancies `0.60` / `0.40`;
- an explicit model number;
- one `HETATM` water which is excluded from the polymer residue contract.

`minimal_identity_gemmi.pdb` is the expected legacy-PDB bridge for the
polymer identity fields. Tests verify the complete polymer atom mapping,
coordinates and occupancies rather than trusting the file name. The fixture is
small enough that malformed author/label mappings, model changes and bridge
renumbering can be created in-memory for fail-closed regression tests.
