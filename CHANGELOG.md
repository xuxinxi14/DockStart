# Changelog

## v0.8.6

- Added `docs/release/release_artifact_profile.md` to describe what a release artifact includes and which usage modes it can support.
- Updated the Windows build script to print a release artifact capability profile during release checks.
- Updated GitHub Release materials to clarify Basic / Assisted / Demo Mode requirements and avoid overclaiming out-of-box capability.
- Enhanced the checksum script with an optional release profile note.
- Kept the change limited to release documentation and scripts; no installer, build output, runtime, or scientific functionality was added.

## v0.8.5

- Added a post-install diagnostic check for app version, OS, Vina, Python, RDKit, Meeko, viewer, resource paths, settings path, demo projects, and Basic/Assisted/Demo availability.
- Added local Markdown diagnostic report export for troubleshooting without network upload.
- Surfaced the diagnostic check and report export on ToolchainStatusPage.
- Documented that diagnostic reports may contain local tool paths and should be reviewed before sharing.
- Kept the feature limited to local inspection; no automatic installation, upload, scientific workflow change, or Vina behavior change was added.

## v0.8.4

- Added structured toolchain repair suggestions for missing Vina, incomplete Python/RDKit/Meeko environments, and Microsoft Store Python warnings.
- Exposed the repair guidance through a Tauri command and surfaced it on ToolchainStatusPage with manual steps and copyable commands.
- Added `docs/toolchain_repair_guide.md` and HelpPage guidance for Basic Mode vs Assisted Mode repair paths.
- Kept the feature offline and advisory only: no automatic installation, PATH mutation, downloads, docking changes, or scientific capability was added.

## v0.8.3

- Improved the first-run guide so new users can choose between existing PDBQT files, raw structure preparation, or demo projects.
- Surfaced current blocking items and next recommended action directly in Dashboard and HelpPage.
- Clarified that missing RDKit/Meeko only blocks Assisted Mode, while Basic Mode remains the lowest-dependency path when Vina and PDBQT inputs are available.
- Kept the change limited to guidance and copy; no automatic installation, docking behavior, or scientific capability was added.

## v0.8.2

- Added small bundled demo project templates for Basic Mode and Assisted Mode.
- Added backend helpers to list, create, and validate demo projects without network access.
- Added Tauri commands and a ProjectCreatePage demo-copy entry.
- Added `docs/demo_projects.md` to explain demo scope, limitations, and why demo files are not scientific evidence.
- Kept demo data tiny and explicitly marked as software-flow examples only; no real docking output or large structures were added.

## v0.8.1

- Strengthened Basic Mode as the minimal dependency path for users who already have receptor/ligand PDBQT files.
- Added a BasicModeGuide component and surfaced it on Dashboard and PDBQT import.
- Updated workflow steps so raw structure download and automatic preparation are optional for Basic Mode rather than hard blockers.
- Clarified ToolchainStatusPage mode impact: Vina controls Basic Mode, RDKit/Meeko controls Assisted Mode.
- Kept changes focused on guidance and UI flow; no scientific capability, docking logic, or Vina behavior changed.

## v0.8.0

- Added an app capability profile for Basic / Assisted / Demo usage modes.
- Added project-level mode recommendation and minimum requirements status helpers.
- Exposed the new capability profile through Tauri commands and surfaced the mode summary on the Dashboard.
- Clarified that RDKit/Meeko missing blocks Assisted Mode only; Basic Mode remains available when Vina and prepared PDBQT files are ready.
- Kept V0.8.0 focused on guidance and capability grading; no scientific workflow, Vina algorithm, PLIP/ProLIF, Open Babel, MGLTools, or drug-efficacy functionality was added.

## v0.6.7

- Performed the V0.6 release packaging freeze audit and unified version numbers to `0.6.7`.
- Updated the GitHub Release template to the V0.6.7 preview version.
- Added installer filename patterns to `.gitignore` while keeping `dist/`, `target/`, runtime binaries, and conda environments excluded.
- Rechecked repository hygiene and confirmed no installer, build output, Python runtime, conda env, real docking output, or third-party source archive is tracked.
- Kept the audit limited to release packaging, documentation, versioning, and repository hygiene; no scientific functionality was added.

## v0.6.6

- Added `docs/release/github_release_template.md` for GitHub Release notes and installation guidance.
- Added `scripts/hash_release_artifacts.py` to calculate local SHA256 checksums and print a Markdown table.
- Updated release checklist and README release-engineering notes for checksum and GitHub Release preparation.
- Kept release preparation limited to documentation and local hashing; no release upload, scientific workflow change, or bundled runtime files were added.

## v0.6.5

- Validated a real Windows Tauri release build and recorded the result in `docs/release/v0_6_5_build_report.md`.
- Produced local MSI and NSIS installer artifacts under `apps/desktop/src-tauri/target/release/bundle/`.
- Fixed the release build script version check for Windows PowerShell 5 and `package-lock.json`.
- Added explicit Tauri bundle icon configuration using the existing local `icons/icon.ico`.
- Confirmed the V0.6.5 lightweight build does not include bundled Vina, bundled Python runtime, conda env, user settings, or real docking outputs.

## v0.6.4

- Added `scripts/build_windows_release.ps1` for repeatable Windows release checks and Tauri build execution.
- Added `npm run build:desktop` as a desktop bundle alias.
- The release script checks branch, clean git state, version consistency, Python tests, frontend build, Cargo check, and optional Tauri bundle output.
- Documented local Windows build commands and artifact locations without committing installers, `dist/`, or `target/`.

## v0.6.3

- Added first-run toolchain guidance to backend toolchain status.
- Added Dashboard first-run checks for AutoDock Vina, Python source, RDKit, and Meeko before a project is created.
- Added ToolchainStatusPage copy-current-Python-path affordance and clearer source explanations.
- Added HelpPage first-use guidance for Vina and RDKit/Meeko conda setup.
- Kept onboarding limited to status and guidance; no tool installation, preparation logic, docking flow, or scientific capability was added.

## v0.6.2

- Added `scripts/export_toolchain_environment.py` to export configured conda Python metadata into a lightweight RDKit/Meeko environment yml.
- Added `docs/release/toolchain_environment.md` and a recommended `environment-dockstart-rdkit-meeko.yml` template.
- Added tests for non-conda structured hints and mocked conda yml generation.
- Updated ToolchainStatusPage guidance for configured Python and missing RDKit/Meeko.
- Reconfirmed DockStart does not auto-install RDKit/Meeko and does not commit conda env, `python.exe`, `Lib/`, `DLLs/`, or `site-packages/`.

## v0.6.1

- Updated bundled Vina resolution to prefer `resources/vina/vina.exe` while retaining `resources/tools/vina/vina.exe` as a legacy fallback.
- Updated `scripts/prepare_bundled_vina.py` to copy local Vina files into `resources/vina/`, compute sha256, update manifest fields, and support `--dry-run`.
- Added manifest `vina` metadata alongside the compatible `bundled_vina` section.
- Expanded ToolchainStatusPage bundled Vina details with manifest source and clearer missing-bundled guidance.
- Kept Vina integration limited to detection and packaging preparation; no docking algorithm, docking run behavior, or scientific result interpretation changed.

## v0.6.0

- Added the V0.6 release engineering structure and packaging strategy docs.
- Added `docs/release/release_strategy.md`, `docs/release/windows_packaging.md`, and `docs/release/release_checklist.md`.
- Added `resources/vina/README.md` as the V0.6 bundled Vina directory placeholder while keeping real Vina binaries ignored.
- Documented Developer build, Lightweight release, Toolchain-assisted release, and future Full offline release boundaries.
- Clarified that V0.6 prepares Windows packaging and release workflow only; it does not add scientific features or bundled conda/RDKit/Meeko runtime files.

## v0.5.9

- Validated the V0.5 frontend shell with Vite and Tauri dev startup.
- Added a local SVG favicon to remove the browser `favicon.ico` 404 during frontend validation.
- Updated ToolchainStatusPage wording so it reflects V0.3 preparation reality: the page detects RDKit/Meeko, while PDBQT preparation is triggered from PreparationPage.
- Hid Sidebar workflow steps until a project is loaded, keeping the no-project dashboard cleaner.
- Added a run-required empty state for run execute, result, and report pages when no `run_id` is selected.
- Kept changes limited to frontend usability, documentation/versioning, and did not alter scientific workflow behavior.

## v0.5.8

- Performed a V0.5 frontend workflow freeze audit and unified version numbers to `0.5.8`.
- Corrected roadmap wording so V0.5 is documented as frontend workflow overhaul, not interaction analysis.
- Clarified that V0.5 keeps backend docking, preparation, viewer data, Vina execution, score parsing, and reporting behavior unchanged.
- Reconfirmed no PLIP/ProLIF, interaction analysis, pocket prediction, drug efficacy judgement, external CDN, large structure/runtime files, or Vina algorithm/scoring changes were added.

## v0.5.7

- Added a built-in HelpPage with a beginner workflow, file-type explanations, page quick reference, and scientific boundary reminders.
- Enabled the sidebar help navigation item and connected HelpPage to App routing.
- Added a lightweight onboarding guide to the empty project dashboard state and dashboard shortcuts.
- Kept changes frontend-only; no backend commands, tool installation, docking behavior, preparation behavior, or viewer science features were added.

## v0.5.6

- Added a shared Vina workflow bar across config generation, run preparation, Vina execution, result parsing, and Markdown report export pages.
- Unified VinaConfigPage and RunPreparePage warning/error presentation with shared warning and command-result components.
- Made run_id context visible across execute/result/report steps.
- Kept Vina config generation, execution, score parsing, and report export backend behavior unchanged.

## v0.5.5

- Reworked ViewerPage into a three-column workspace: left controls, central 3D canvas, and right inspection panel.
- Grouped structure loading, docking pose list loading, and Box visualization controls without changing viewer backend commands.
- Added clearer file-status and pose-status presentation, including unified warnings and command-result details.
- Kept ViewerPage limited to geometry viewing, Box synchronization, and pose display; no PLIP/ProLIF, interaction analysis, pocket prediction, or drug efficacy judgement was added.

## v0.5.4

- Improved StructureFetchPage information hierarchy around receptor/ligand raw files.
- Improved PreparationPage copy and grouping so raw inputs, prepared outputs, toolchain status, preparation actions, logs, and warnings are clearer.
- Reused unified warning, disclaimer, and command-result components on raw/preparation pages.
- Kept raw downloading and RDKit/Meeko preparation backend behavior unchanged.

## v0.5.3

- Added unified status, warning, disclaimer, command-result, log-preview, file-status, run-status, report-status, and tool-status presentation components.
- Started replacing page-local warning/error/status markup in Dashboard, ResultPage, ReportPage, and RunExecutePage.
- Centralized scientific disclaimer text for score, preparation, and viewer limitations.
- Kept changes presentation-only and did not alter Vina execution, score parsing, preparation, viewer loading, or backend commands.

## v0.5.2

- Added a guided workflow stepper that maps DockStart project state to create/raw/preparation/Box/Vina/config/run/result/report/viewer steps.
- Dashboard now shows the full workflow stepper with action buttons for each step.
- Sidebar can show a compact workflow status summary when project workflow data has been loaded.
- Workflow status is derived from existing project status fields; no backend scientific capability or Vina behavior was changed.

## v0.5.1

- Added ProjectDashboardPage as the main project entry point after a project is created.
- Dashboard reads existing `get_project_workflow_status` data to show raw, prepared, Box, Vina, config, latest run, and report readiness.
- Added next recommended action, shortcut cards, project metadata, and scientific risk reminders.
- Kept backend scientific workflow unchanged and did not add new docking, preparation, viewer, or analysis capability.

## v0.5.0

- Added the frontend AppShell foundation with a persistent Sidebar, Topbar, shared page content area, and workflow summary.
- Added reusable UI building blocks: PageHeader, SectionCard, StatusBadge, ActionButton, ErrorPanel, EmptyState, FilePathText, and WorkflowStepper.
- Added navigation metadata and a small workflow-summary utility so existing pages can be reached through a unified shell.
- Kept existing backend commands and scientific workflows unchanged.
- Did not add PLIP/ProLIF, interaction analysis, drug efficacy judgement, Open Babel, MGLTools, external CDN usage, or Vina algorithm changes.

## v0.4.6

- Performed a V0.4 viewer workflow freeze audit.
- Unified project version numbers to `0.4.6`.
- Clarified current viewer documentation so V0.4 is no longer described as future-only 3D work.
- Reconfirmed ViewerPage is only for geometry review, Box synchronization, and docking pose inspection, not PLIP/ProLIF analysis, pocket prediction, interaction interpretation, scientific validation, or drug efficacy judgement.
- Reconfirmed no Vina algorithm/scoring changes, external CDN resources, large structure files, real docking outputs, or Python runtime binaries were added.

## v0.4.5

- Documented the V0.4 viewer workflow across README, user guide, smoke tests, FAQ, roadmap, PROJECT, and CLAUDE guidance.
- Added smoke-test guidance for opening ViewerPage, checking raw/prepared structures, saving Box overlay parameters, and viewing docking poses after a run.
- Clarified that ViewerPage does not perform PLIP/ProLIF analysis, pocket prediction, interaction interpretation, molecular dynamics, or drug efficacy judgement.
- Clarified that 3Dmol.js is used through npm-managed local dependencies and no external CDN is required.
- Did not add new runtime behavior beyond documentation and version updates.

## v0.4.4

- Added viewer capability fields to `get_project_workflow_status`, including raw/prepared/docking-output visibility and available run outputs.
- Added recommended viewer actions such as preview raw, view prepared files and Box, or inspect docking poses.
- Added minimal workflow entry points from BoxSetupPage and ResultPage into ViewerPage.
- Fixed `backend/tests/test_project.py` so `unittest.mock` is imported explicitly and the test module can run on its own.
- Did not change existing workflow status field semantics, Vina execution, score parsing, or report export behavior.

## v0.4.3

- Enhanced docking pose parsing for `runs/{run_id}/out.pdbqt`, including multi-`MODEL` files and single-pose files without `MODEL` records.
- Added pose score summary loading from `runs/{run_id}/scores.csv` when available.
- ViewerPage can list pose modes, show affinity/rmsd summaries, and load a selected mode for 3D viewing with prepared receptor context.
- Missing `scores.csv` no longer blocks pose viewing; DockStart shows a warning and keeps geometry viewing available.
- Did not modify `out.pdbqt`, call Vina, add PLIP/ProLIF, perform interaction analysis, or make drug efficacy judgements.

## v0.4.2

- Added backend Box visualization payloads for center, size, min/max, corners, and 3Dmol box overlay data.
- Added backend/Tauri commands to read Box visualization data and save Box updates back to `project.json`.
- Updated ViewerPage with six Box inputs, live overlay refresh, warning display for large boxes, and save/reload controls.
- Reused the existing Box validation rules and `project.json.box` field, so BoxSetupPage and ViewerPage stay synchronized.
- Did not add pocket detection, automatic Box recommendation, Vina config semantic changes, RDKit/Meeko calls, or Vina execution changes.

## v0.4.1

- Added a minimal local 3Dmol.js ViewerPage with structure source selection, load, clear, and zoom-to-fit controls.
- Added ViewerPage entry points from the home flow, PreparationPage, and ImportPdbqtPage.
- ViewerPage can request receptor raw, ligand raw, prepared receptor, prepared ligand, and latest docking output content through the backend viewer commands.
- Added local npm-managed `3dmol` dependency; no external CDN is used.
- Did not add Box overlay, docking pose score mapping, PLIP/ProLIF, interaction analysis, pocket prediction, drug efficacy judgement, or Vina algorithm changes.

## v0.4.0

- Added backend viewer data models and project-local structure file loading for raw receptor, raw ligand, prepared receptor, prepared ligand, and docking output files.
- Added safe viewer path validation that rejects absolute paths, path traversal, empty files, and structure files larger than 20 MB.
- Added docking pose text listing/loading from `runs/{run_id}/out.pdbqt`, including single-pose fallback when `MODEL` records are absent.
- Added Tauri commands for viewer file status, structure loading, docking pose listing, and docking pose loading.
- Did not add PLIP/ProLIF, interaction analysis, pocket prediction, drug efficacy judgement, Vina algorithm changes, or frontend 3D rendering yet.

## v0.3.9

- Created and validated a dedicated `dockstart-rdkit-meeko` conda environment for DockStart RDKit/Meeko preparation testing.
- Configured DockStart local settings to use the conda environment Python as `configured` Python; `dockstart_settings.json` remains ignored and uncommitted.
- Confirmed RDKit `2026.03.3` and Meeko `0.7.1` are detected, with ligand and receptor preparation capabilities available.
- Successfully generated real temporary `prepared/ligand.pdbqt` and `prepared/receptor.pdbqt` from raw SDF/PDB samples, with preparation metadata/stdout/stderr/command/input/output records.
- Fixed the RDKit inline SDF capability probe sample so real RDKit reports SDF read capability as `ok`.
- Documented the recommended conda/mamba environment setup, the Microsoft Store Python caveat, and the Meeko receptor CLI compatibility need for `pkg_resources` / `setuptools<81`.
- Did not add Open Babel, PLIP, MGLTools, 3D visualization, interaction analysis, drug efficacy judgement, Vina algorithm changes, SSL changes, or Python runtime binaries.

## v0.3.8

- Validated V0.3 preparation toolchain behavior against the real local Python environment.
- Confirmed missing RDKit/Meeko environments return structured `missing` results instead of continuing preparation.
- Fixed Windows subprocess UTF-8 decoding for RDKit/Meeko capability detection and preparation command execution.
- Added mock coverage for empty subprocess stdout/stderr and Python paths containing spaces.
- Clarified docs that V0.3 automatic preparation depends on a user-configured Python environment with RDKit/Meeko installed; DockStart does not auto-install those packages.
- Did not add Open Babel, PLIP, MGLTools, 3D visualization, interaction analysis, drug efficacy judgement, Vina algorithm changes, or Python runtime binaries.

## v0.3.7

- Performed V0.3 preparation workflow freeze audit.
- Unified project version numbers to `0.3.7`.
- Clarified historical V0.2/V0.3 wording so old "current" statements do not imply missing features after V0.3.6.
- Reconfirmed no Open Babel, PLIP, MGLTools, 3D visualization, interaction analysis, drug efficacy judgement, Vina algorithm changes, Python runtime binaries, or SSL changes were added.

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
