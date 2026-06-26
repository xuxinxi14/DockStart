# Changelog

## v0.3.6

- Documented the V0.3 automated PDBQT preparation workflow from raw downloads through preparation, box/config/run, result parsing, and Markdown report export.
- Added a mock preparation smoke test that checks raw receptor/ligand records can become prepared PDBQT files and then generate `vina_config.txt` without real RDKit/Meeko or Vina.
- Clarified preparation limits: protonation, charges, conformers, missing residues, waters, metals, cofactors, and chain choices still require scientific review.
- Clarified that V0.3 does not add Open Babel, MGLTools, PLIP, 3D visualization, interaction analysis, molecular dynamics, PDF reports, or drug efficacy judgement.

## v0.3.5

- Added auditable preparation run records under `preparation/{target}_{NNN}/`.
- Each ligand/receptor preparation execution now writes `metadata.json`, `stdout.txt`, `stderr.txt`, `command.json`, `input_snapshot.json`, and `output_check.json`.
- Added `latest_preparation` tracking in `project.json`.
- Added backend/Tauri entry points to list preparation runs, load preparation metadata, and read the latest preparation record.
- Kept failed preparation attempts traceable with metadata and stdout/stderr.
- Did not call AutoDock Vina, parse docking results, add Open Babel/PLIP/MGLTools, add 3D visualization, or make drug efficacy judgements.

## v0.3.4

- Connected preparation state back into the existing config/run prerequisite checks without changing the Vina config, execution, parsing, or report semantics.
- Added structured Chinese hints when raw receptor/ligand files exist but `prepared/receptor.pdbqt` or `prepared/ligand.pdbqt` is still missing.
- Added preparation-failed hints that point users back to preparation logs before generating config or preparing a run.
- Added `get_project_workflow_status` backend/Tauri entry point and a minimal PreparationPage next-action display.
- Added mock-friendly backend tests for raw-but-not-prepared, preparation-failed, workflow next action, and old-project compatibility.
- Did not add Open Babel, PLIP, MGLTools, 3D visualization, interaction analysis, drug efficacy judgement, or Vina algorithm changes.

## v0.3.3

- Added receptor raw PDB/CIF to `prepared/receptor.pdbqt` preparation through detected Meeko receptor CLI.
- Added receptor preparation validation, command construction, stdout/stderr/log capture, and project.json updates.
- Added Tauri commands and a minimal PreparationPage button for receptor preparation.
- Default behavior does not overwrite existing `prepared/receptor.pdbqt`; overwrite must be explicitly enabled.
- Added mock-first backend tests; tests do not depend on a real Meeko installation.
- Did not add Open Babel, MGLTools, MOL2/SMILES preparation, 3D visualization, interaction analysis, drug efficacy judgement, or Vina workflow changes.

## v0.3.2

- Added ligand raw SDF/MOL to `prepared/ligand.pdbqt` preparation through the resolved Python + RDKit + Meeko toolchain.
- Added ligand preparation validation, safe helper-script generation, stdout/stderr/log capture, and project.json updates.
- Added Tauri commands and a minimal PreparationPage button for ligand preparation.
- Default behavior does not overwrite existing `prepared/ligand.pdbqt`; overwrite must be explicitly enabled.
- Added mock-first backend tests; tests do not depend on a real RDKit/Meeko installation.
- Did not add receptor preparation, MOL2/SMILES preparation, Open Babel, PLIP, MGLTools, 3D visualization, drug efficacy judgement, or Vina workflow changes.

## v0.3.1

- Added RDKit preparation capability detection for import, version, and inline SDF read probing.
- Added Meeko capability detection for import, version, and candidate ligand/receptor preparation API or CLI discovery.
- Added `get_preparation_tool_status` backend/Tauri entry point.
- Updated PreparationPage to show RDKit/Meeko preparation capability status and Python source.
- Did not install packages, generate PDBQT, run RDKit/Meeko molecule processing, or change the Vina workflow.
- Did not add Open Babel, PLIP, MGLTools, 3D visualization, drug efficacy judgement, or Vina algorithm changes.

## v0.3.0

- Added preparation workflow data models for receptor and ligand PDBQT preparation.
- Added `preparation` state to `project.json` while keeping old projects compatible.
- Added backend status, prerequisite-check, and reset helpers for preparation workflow.
- Added minimal Tauri commands and a minimal PreparationPage entry.
- Clarified that this stage does not execute RDKit/Meeko molecule processing yet.
- Did not add Open Babel, PLIP, MGLTools, 3D visualization, drug efficacy judgement, or Vina workflow changes.

## v0.2.10

- Expanded `docs/smoke_test.md` with separate V0.1 local prepared PDBQT and V0.2 raw download smoke tests.
- Documented expected raw outputs: `raw/receptor_{PDB_ID}.pdb` or `.cif`, and `raw/ligand_{cid}.sdf`.
- Documented expected prepared inputs: `prepared/receptor.pdbqt` and `prepared/ligand.pdbqt`.
- Clarified that raw files are not prepared PDBQT files and cannot directly run in AutoDock Vina.
- Updated release notes and roadmap language for the completed V0.2 raw workflow documentation pass.
- Kept RDKit/Meeko automatic preparation as a future V0.3 design topic.
- Did not add PDBQT auto-generation, RDKit/Meeko processing, Open Babel/PLIP/MGLTools, 3D visualization, or Vina workflow changes.

## v0.2.9

- Added `docs/manual_pdbqt_preparation.md`.
- Documented raw files, prepared PDBQT files, and why AutoDock Vina needs PDBQT.
- Documented why downloaded PDB/CIF/SDF files cannot directly run in Vina.
- Listed optional external preparation tools: Meeko, AutoDockTools/MGLTools, and Open Babel.
- Clarified license boundaries: Open Babel, MGLTools, and PLIP are not bundled; Meeko/RDKit remain detection-only.
- Stated that DockStart currently does not guarantee scientific correctness of externally generated PDBQT files.
- Did not add PDBQT auto-generation, RDKit/Meeko processing, Open Babel/PLIP/MGLTools, 3D visualization, or Vina workflow changes.

## v0.2.8

- Clarified the raw-to-prepared workflow across the desktop UI.
- Added a home-page flow: download raw structures, manually prepare PDBQT, import prepared PDBQT, set parameters, run Vina.
- Strengthened ProjectCreatePage guidance for the two entry points: raw download or direct PDBQT import.
- Added ImportPdbqtPage guidance explaining raw files versus prepared PDBQT files.
- Added StructureFetchPage next-step guidance to manually prepare and import PDBQT after raw download.
- Added ToolchainStatusPage guidance that Meeko/RDKit are detection-only and do not process molecules.
- Did not add PDBQT auto-generation, RDKit/Meeko processing, Open Babel/PLIP/MGLTools, 3D visualization, or Vina workflow changes.

## v0.2.7

- Added PubChem ligand lookup by compound name, saved as `raw/ligand_name_{name}.sdf`.
- Kept PubChem CID lookup compatible with the existing `raw/ligand_{cid}.sdf` path.
- Added a structured SMILES placeholder that returns a Chinese "temporarily unsupported" error without calling RDKit or the network.
- Added explicit tests for RCSB `.cif` naming, PubChem name lookup, and SMILES unsupported behavior.
- Updated StructureFetchPage with PubChem query type selection: CID, name, and SMILES placeholder.
- Did not add PDBQT auto-generation, RDKit/Meeko processing, Open Babel/PLIP/MGLTools, 3D visualization, or Vina workflow changes.

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
