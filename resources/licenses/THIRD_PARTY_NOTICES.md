# Third Party Notices

DockStart is a third-party open-source Chinese molecular docking workbench based
on AutoDock Vina workflows. DockStart does not claim to be an official AutoDock
Vina distribution.

DockStart's own source code is licensed under Apache-2.0. The complete text is
packaged as `resources/licenses/DockStart-Apache-2.0.txt`. That license does not
replace the licenses of the third-party components below.

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

## Frontend runtime

- 3Dmol.js 2.5.5: molecular structure rendering; BSD-3-Clause; license text in
  `resources/licenses/3Dmol_LICENSE.txt`.
- React and React DOM 19.x: desktop UI runtime; MIT; license texts in
  `resources/licenses/React_LICENSE.txt` and
  `resources/licenses/React-DOM_LICENSE.txt`.

## Tauri desktop runtime

- Tauri and `@tauri-apps/api`: desktop shell/runtime; Apache-2.0 OR MIT.
- `tauri-plugin-dialog`: native file/directory dialogs; MIT OR Apache-2.0.
- serde 1.x and serde_json 1.x: structured desktop task events; MIT OR
  Apache-2.0. The packaged copy uses the MIT terms in
  `resources/licenses/Serde_LICENSE-MIT.txt`.
- Full license texts:
  `resources/licenses/Tauri_LICENSE_APACHE-2.0.txt` and
  `resources/licenses/Tauri_LICENSE_MIT.txt`.
- Plugin SPDX provenance:
  `resources/licenses/Tauri-plugin-dialog_LICENSE.spdx`.

## Python Runtime

- Purpose: optional bundled runtime for DockStart's Python backend.
- License: Python Software Foundation License.
- Integration mode: optional bundled runtime under `resources/python/`.
- Default repository state: `python.exe`, `Lib/`, `DLLs/`, and `Scripts/` are
  ignored by Git and prepared only for local release packages.

If a release package includes a Python runtime, keep the Python license file and
runtime source/version metadata with the packaged application.

## Bundled Python Packages

DockStart v0.10.2 has two isolated Windows release profiles. Basic Stable uses a
backend-only Python runtime and excludes `Lib/site-packages` and `Scripts`.
Assisted Stable adds the following pinned, ordinary-directory Python packages
for local PDBQT preparation. They are not frozen into `dockstart-desktop.exe`:

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
- tqdm 4.67.1
  - Purpose: progress utility used by the preparation toolchain.
  - License expression retained from wheel metadata: MPL-2.0 AND MIT.
- tomli 2.2.1
  - Purpose: TOML compatibility fallback.
  - License: MIT.
- colorama 0.4.6
  - Purpose: tqdm Windows conditional dependency.
  - License: BSD-3-Clause.

The runtime wheels are not committed to Git. The Assisted release builder must
keep their distribution metadata and original license/notices under
`resources/licenses/python-packages/`. It must also ship the exact upstream
source archives for Meeko 0.7.1, Gemmi 0.7.5, and tqdm 4.67.1 together with
`resources/sources/SOURCE_MANIFEST.json`. DockStart does not modify those three
components in v0.10.2.

## Generated production dependency bundle

Every Basic and Assisted Windows release also contains the target-specific
Cargo and production npm dependency inventory at
`resources/licenses/dependencies/THIRD_PARTY_DEPENDENCIES.json`. The adjacent
`cargo/` and `npm/` directories preserve the license, copying, notice, and
copyright files supplied by each resolved package, with a SHA256 for every
copied file. MPL-2.0 crates and crates without a source license file additionally
include the exact Cargo archive verified against the committed `Cargo.lock`.
The release verifier rejects missing, extra, or hash-mismatched bundle files.
