# Changelog

## v0.2.6

- Enhanced raw file status reporting for receptor and ligand records.
- Added `size_bytes`, `modified_at`, `absolute_path`, and `record_consistent` metadata to raw file checks.
- Added backend helpers and Tauri commands to clear receptor/ligand raw records.
- Kept prepared PDBQT paths and files untouched when clearing raw records.
- Allowed optional raw file deletion only for files inside the project `raw/` directory.
- Updated StructureFetchPage with raw status cards, overwrite warnings, clear-record actions, and raw/prepared guidance.
- Did not add PDBQT auto-generation, RDKit/Meeko processing, Open Babel/PLIP/MGLTools, 3D visualization, or Vina workflow changes.

## v0.2.5

- Added raw receptor structure download from RCSB PDB by PDB ID.
- Added raw ligand SDF download from PubChem by CID.
- Saved downloaded raw files under each project `raw/` directory.
- Recorded `source`, `source_id`, and `raw_file` in `project.json` while keeping prepared PDBQT paths in `file`.
- Did not add PDBQT auto-generation, RDKit/Meeko processing, Open Babel/PLIP/MGLTools, 3D visualization, or Vina workflow changes.

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
