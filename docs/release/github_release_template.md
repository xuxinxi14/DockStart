# DockStart v0.9.6 Local Full Candidate

DockStart is a Windows molecular docking workbench based on AutoDock Vina. It provides a Chinese-first desktop workflow for raw structure acquisition, RDKit/Meeko-assisted PDBQT preparation, Vina execution, 3D viewing, score parsing, and Markdown report export.

## Included

- DockStart desktop app.
- Project workflow UI and dashboard.
- RCSB/PubChem raw structure workflow.
- RDKit/Meeko preparation workflow using the verified bundled Full Python runtime, while still allowing a user-configured preparation environment.
- AutoDock Vina config, run preparation, execution, score parsing, and report export.
- 3D structure viewer, docking box visualization, and docking pose viewing.
- First-run toolchain guidance.
- Basic / Assisted / Demo Mode guidance.
- Demo project templates for software-flow learning.
- Post-install diagnostic check and local Markdown diagnostic report export.

## Capability Profile

- **Basic Mode**: available through bundled AutoDock Vina when users provide receptor/ligand PDBQT files.
- **Assisted Mode**: the local Full candidate includes bundled Python, RDKit, and Meeko; preparation output still requires user inspection.
- **Demo Mode**: available when bundled demo project templates are present. Demo data is for software workflow demonstration only.

The Full local candidate reduces setup work but does not imply that molecular preparation is scientifically correct without user inspection.

## Not Included

- No drug efficacy judgment.
- No PLIP or ProLIF integration.
- No interaction analysis.
- No molecular dynamics.
- No pocket prediction.
- No automatic scientific validation.
- No AutoDock Vina algorithm or scoring-function modifications.
- No bundled conda environment.
- No automatic mutation of user Python environments.
- No Open Babel or MGLTools integration.

## Installation Notes

1. Download the Windows installer artifact attached to this release.
2. Run the installer and start DockStart.
3. Open the toolchain or first-run guide.
4. Confirm bundled AutoDock Vina through the toolchain page, or choose an external configured path.
5. Confirm the bundled Full Python preparation runtime, or choose a user-configured Python environment.
6. Use the post-install diagnostic check to confirm Basic / Assisted / Demo availability.
7. Create or open a DockStart project.

Optional external Python toolchain:

```powershell
conda create -n dockstart-rdkit-meeko -c conda-forge python=3.11 rdkit meeko numpy scipy
```

Then configure that environment's `python.exe` in DockStart settings.

## Known Limitations

- The bundled RDKit/Meeko runtime reduces setup work but does not validate protonation, charge, conformer, chain, water, metal, or cofactor choices.
- Meeko ligand/receptor preparation capability depends on the installed Meeko version.
- Generated PDBQT files still require user inspection for protonation, charge, conformer, chain, missing residue, water, metal, and cofactor choices.
- Docking score is only a structural trend reference and cannot prove real binding, efficacy, safety, or clinical value.
- 3Dmol may produce a build-time `eval` warning; this is a known dependency warning.
- The frontend bundle may exceed the default Vite chunk-size warning threshold because the viewer library is included locally rather than loaded from a CDN.

## Checksums

Replace the placeholders below before publishing the release:

| File | Size | SHA256 |
| --- | ---: | --- |
| `DockStart_0.9.6_x64_en-US.msi` | TBD | TBD |
| `DockStart_0.9.6_x64-setup.exe` | TBD | TBD |

You can generate this table with:

```powershell
python scripts/hash_release_artifacts.py path\to\DockStart_0.9.6_x64_en-US.msi path\to\DockStart_0.9.6_x64-setup.exe --include-profile-note
```

## Scientific Disclaimer

DockStart and AutoDock Vina outputs are computational predictions for a specific input structure, docking box, parameter set, and tool version. Docking scores and poses are not experimental evidence and must not be used as direct proof of real binding or drug efficacy.
