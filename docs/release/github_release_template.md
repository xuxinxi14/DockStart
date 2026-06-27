# DockStart v0.6.6 Preview

DockStart is a Windows molecular docking workbench based on AutoDock Vina. It provides a Chinese-first desktop workflow for raw structure acquisition, RDKit/Meeko-assisted PDBQT preparation, Vina execution, 3D viewing, score parsing, and Markdown report export.

## Included

- DockStart desktop app.
- Project workflow UI and dashboard.
- RCSB/PubChem raw structure workflow.
- RDKit/Meeko preparation workflow, using a user-configured Python environment.
- AutoDock Vina config, run preparation, execution, score parsing, and report export.
- 3D structure viewer, docking box visualization, and docking pose viewing.
- First-run toolchain guidance.

## Not Included

- No drug efficacy judgment.
- No PLIP or ProLIF integration.
- No interaction analysis.
- No molecular dynamics.
- No pocket prediction.
- No automatic scientific validation.
- No AutoDock Vina algorithm or scoring-function modifications.
- No bundled conda environment, RDKit/Meeko site-packages, or Python runtime in the lightweight release.

## Installation Notes

1. Download the Windows installer artifact attached to this release.
2. Run the installer and start DockStart.
3. Open the toolchain or first-run guide.
4. Configure AutoDock Vina if bundled Vina is not included.
5. Configure a Python environment with RDKit and Meeko if you want automatic PDBQT preparation.
6. Create or open a DockStart project.

Recommended Python toolchain:

```powershell
conda create -n dockstart-rdkit-meeko -c conda-forge python=3.11 rdkit meeko numpy scipy
```

Then configure that environment's `python.exe` in DockStart settings.

## Known Limitations

- RDKit/Meeko must be installed or configured by the user for automatic PDBQT preparation.
- Meeko ligand/receptor preparation capability depends on the installed Meeko version.
- Generated PDBQT files still require user inspection for protonation, charge, conformer, chain, missing residue, water, metal, and cofactor choices.
- Docking score is only a structural trend reference and cannot prove real binding, efficacy, safety, or clinical value.
- 3Dmol may produce a build-time `eval` warning; this is a known dependency warning.
- The frontend bundle may exceed the default Vite chunk-size warning threshold because the viewer library is included locally rather than loaded from a CDN.

## Checksums

Replace the placeholders below before publishing the release:

| File | Size | SHA256 |
| --- | ---: | --- |
| `DockStart_0.6.6_x64_en-US.msi` | TBD | TBD |
| `DockStart_0.6.6_x64-setup.exe` | TBD | TBD |

You can generate this table with:

```powershell
python scripts/hash_release_artifacts.py path\to\DockStart_0.6.6_x64_en-US.msi path\to\DockStart_0.6.6_x64-setup.exe
```

## Scientific Disclaimer

DockStart and AutoDock Vina outputs are computational predictions for a specific input structure, docking box, parameter set, and tool version. Docking scores and poses are not experimental evidence and must not be used as direct proof of real binding or drug efficacy.
