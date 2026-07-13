# DockStart v0.9.7 Basic Stable

DockStart is a Windows molecular docking workbench based on AutoDock Vina. This Basic Stable release provides a Chinese-first desktop workflow for users who already have receptor and ligand PDBQT files: docking-box setup, Vina execution, 3D viewing, score parsing, and Markdown report export.

## Included

- DockStart desktop app and project workflow UI.
- Bundled AutoDock Vina for the existing-PDBQT workflow.
- Bundled backend-only Python runtime used by the DockStart desktop backend.
- AutoDock Vina config generation, run preparation, execution, score parsing, and report export.
- 3D structure viewer, docking-box visualization, and docking-pose viewing.
- Basic / Assisted / Demo Mode guidance.
- Demo project templates for software-flow learning.
- Post-install diagnostic check and local Markdown diagnostic report export.
- Assisted Mode UI and adapters for a separately configured Python environment.

## Capability Profile

- **Basic Mode**: ready when the bundled Vina is available and the user provides receptor/ligand PDBQT files.
- **Assisted Mode**: the interface remains available, but this release does not bundle RDKit or Meeko. Users must configure a separate Python environment containing both packages.
- **Demo Mode**: available when bundled demo templates are present. Demo data is for software workflow demonstration only.

“Out of the box” in this release refers to Basic Mode with prepared PDBQT inputs. It does not mean that raw molecular structures can be prepared without an external RDKit/Meeko environment.

## Not Included

- No bundled RDKit or Meeko preparation runtime.
- No bundled conda environment.
- No automatic installation or mutation of user Python environments.
- No automatic scientific validation of prepared structures.
- No drug-efficacy judgment.
- No PLIP or ProLIF integration.
- No interaction analysis, molecular dynamics, or pocket prediction.
- No Open Babel or MGLTools integration.
- No AutoDock Vina algorithm or scoring-function modifications.

## Installation Notes

1. Download the Windows installer attached to this release.
2. Run the installer and start DockStart.
3. Open the toolchain page and confirm the bundled AutoDock Vina status.
4. For Basic Mode, create a project and import prepared receptor/ligand PDBQT files.
5. Use the post-install diagnostic check to confirm Basic / Assisted / Demo availability.
6. Only if Assisted Mode is needed, configure a separate Python environment with RDKit and Meeko.

Optional Assisted Mode environment:

```powershell
conda create -n dockstart-rdkit-meeko -c conda-forge python=3.11 rdkit meeko numpy scipy
```

Then configure that environment's `python.exe` in DockStart settings. The environment is user-managed and is not part of the v0.9.7 installer.

## Known Limitations

- Assisted Mode availability depends on the configured Python, RDKit, and Meeko versions and detected preparation capabilities.
- Generated PDBQT files require user inspection for protonation, charge, conformer, chain, missing residue, water, metal, and cofactor choices.
- Docking scores and poses cannot prove real binding, efficacy, safety, or clinical value.
- 3Dmol may produce a build-time `eval` warning; this is a known dependency warning.
- The frontend bundle may exceed Vite's default chunk-size warning threshold because the viewer library is packaged locally instead of loaded from a CDN.

## Checksums

Replace the placeholders below before publishing the release:

| File | Size | SHA256 |
| --- | ---: | --- |
| `DockStart_0.9.7_x64_en-US.msi` | TBD | TBD |
| `DockStart_0.9.7_x64-setup.exe` | TBD | TBD |

Generate the table with:

```powershell
python scripts/hash_release_artifacts.py path\to\DockStart_0.9.7_x64_en-US.msi path\to\DockStart_0.9.7_x64-setup.exe --include-profile-note
```

## Scientific Disclaimer

DockStart and AutoDock Vina outputs are computational predictions for a specific input structure, docking box, parameter set, and tool version. Docking scores and poses are not experimental evidence and must not be used as direct proof of real binding or drug efficacy.
