# Third Party Notices

DockStart is a third-party open-source Chinese molecular docking workbench based
on AutoDock Vina workflows. DockStart does not claim to be an official AutoDock
Vina distribution.

## AutoDock Vina

- Purpose: docking engine used by DockStart MVP workflows.
- License: Apache License 2.0.
- Integration mode: optional external executable or optional bundled executable.
- Default repository state: `vina.exe` is not committed by default.
- Expected bundled path, when provided by a distributor: `resources/vina/vina.exe`.

If a release package includes AutoDock Vina binaries, keep this notice and any
required upstream license files together with the packaged application.

## Phosphor Icons React

- Version: 2.1.10.
- Purpose: navigation, state, and action icons in the DockStart desktop UI.
- License: MIT.
- Integration mode: npm dependency bundled by Vite.
- License text: `resources/licenses/Phosphor-Icons_LICENSE.txt`.

## Python Runtime

- Purpose: optional bundled runtime for DockStart's Python backend.
- License: Python Software Foundation License.
- Integration mode: optional bundled runtime under `resources/python/`.
- Default repository state: `python.exe`, `Lib/`, `DLLs/`, and `Scripts/` are
  ignored by Git and prepared only for local release packages.

If a release package includes a Python runtime, keep the Python license file and
runtime source/version metadata with the packaged application.

## Bundled Python Packages

When DockStart Full packages include `resources/python/`, the runtime may also
include Python packages used for local PDBQT preparation:

- RDKit 2026.3.3
  - Purpose: ligand structure reading and preparation support.
  - License: BSD-3-Clause.
- Meeko 0.7.1
  - Purpose: receptor and ligand PDBQT preparation for AutoDock Vina workflows.
  - License: LGPL-2.1 or later.
- NumPy 1.26.4
  - Purpose: numeric dependency used by scientific Python packages.
  - License: BSD-3-Clause.
- SciPy 1.17.1
  - Purpose: Meeko dependency.
  - License: BSD-3-Clause, with additional notices for bundled numerical
    runtime libraries in the wheel metadata.
- Pillow 12.2.0
  - Purpose: RDKit wheel dependency.
  - License expression from wheel metadata: MIT-CMU.
- Gemmi 0.7.5
  - Purpose: Meeko dependency.
  - License: MPL-2.0.
- ProDy 2.4.1
  - Purpose: Meeko ProDy reader support for receptor preparation.
  - License: MIT.
- Biopython 1.87
  - Purpose: ProDy dependency.
  - License expression from wheel metadata:
    LicenseRef-Biopython-License-Agreement.
- pyparsing 3.3.2
  - Purpose: ProDy dependency.
  - License: MIT.

These packages are not committed to Git by default. Release builders that bundle
`resources/python/` must keep package metadata, license files, and this notice
with the packaged application.
