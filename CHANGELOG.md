# Changelog

## v0.2.4

- Clarified the V0.2 roadmap around the bundled Python runtime and toolchain documentation.
- Split V0.2 planning into Toolchain line and Structure acquisition line.
- Documented that PDB/PubChem download, PDBQT auto-generation, RDKit ligand processing, and Meeko receptor/ligand preparation are not yet implemented.

## v0.2.3

- Added bundled Python runtime path resolution and integrity checks.
- Added `resources/python/README.md` and `scripts/prepare_bundled_python.py`.
- Added `bundled_python` metadata in `resources/toolchain_manifest.json`.
- Updated ToolchainStatusPage to show bundled Python status, version, sha256, resolved Python source, and Meeko/RDKit import-check Python source.
- Did not commit `python.exe` or a full Python runtime.

## v0.1.11

- Added MVP documentation and workflow guidance after the local PDBQT docking loop.
- Clarified DockStart Full toolchain direction and license boundaries.
- Did not add PDB/PubChem download or automatic receptor/ligand preparation.

## v0.1.10

- Added Markdown docking report export.
- The report records project files, Vina parameters, run metadata, score table, and scientific disclaimer.
