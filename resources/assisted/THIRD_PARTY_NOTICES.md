# DockStart Assisted Stable - Third Party Notices

This notice applies only to the `assisted_stable` distribution profile. The
Basic profile does not distribute RDKit, Meeko, or these scientific Python
packages.

The Assisted runtime is a normal, separate Python 3.11 directory under
`resources/python/`. It is not frozen into `dockstart-desktop.exe`. DockStart
invokes Meeko through a Python subprocess and file-based adapter boundary.
Users may configure another compatible Python environment, which has priority
for preparation tasks. Integrity hashes are provenance and diagnostics only;
they must not be used to prevent replacement of Meeko or Gemmi.

| Component | Version | Purpose | License |
| --- | --- | --- | --- |
| AutoDock Vina | 1.2.7 | Docking engine | Apache-2.0 |
| CPython | 3.11.x | DockStart backend and preparation runtime | Python Software Foundation License |
| Meeko | 0.7.1 | Receptor and ligand PDBQT preparation | LGPL-2.1; wheel classifier also identifies LGPLv2+ |
| RDKit | 2026.3.3 | Ligand structure parsing | BSD-3-Clause |
| NumPy | 1.26.4 | Scientific runtime dependency | BSD-3-Clause plus notices embedded in the wheel license |
| SciPy | 1.17.1 | Meeko spatial dependency | BSD-3-Clause plus notices embedded in the wheel license |
| Gemmi | 0.7.5 | Receptor chemistry dependency | MPL-2.0 |
| Pillow | 12.2.0 | RDKit wheel dependency | MIT-CMU |
| tqdm | 4.67.1 | Progress utility | MPL-2.0 AND MIT, exactly as declared by wheel metadata |
| tomli | 2.2.1 | TOML compatibility fallback | MIT |
| colorama | 0.4.6 | tqdm Windows dependency | BSD-3-Clause |
| 3Dmol.js | 2.5.5 | Molecular structure rendering | BSD-3-Clause |
| React / React DOM | 19.x | Desktop UI runtime | MIT |
| Phosphor Icons React | 2.1.10 | Desktop navigation and action icons | MIT |
| Tauri | 2.x | Desktop shell/runtime | Apache-2.0 OR MIT |
| tauri-plugin-dialog | 2.x | Native file/directory dialogs | MIT OR Apache-2.0 |
| serde / serde_json | 1.x | Structured desktop task events | MIT OR Apache-2.0 |

The complete Meeko 0.7.1, Gemmi 0.7.5, and tqdm 4.67.1 source archives are
packaged under `resources/sources/`. Their hashes and official PyPI artifact
URLs are recorded in `resources/sources/SOURCE_MANIFEST.json`. No DockStart
patches are applied to these packages.

Upstream project references:

- Meeko / Forli Lab: <https://github.com/forlilab/Meeko>
- RDKit: <https://www.rdkit.org/>
- Gemmi: <https://github.com/project-gemmi/gemmi>
- tqdm: <https://github.com/tqdm/tqdm>

The recorded `files.pythonhosted.org` URLs identify the exact PyPI artifacts
used for this build; they do not imply that every wheel was built by DockStart
or endorsed by the upstream project.

Package-specific license files are available under
`resources/licenses/python-packages/` and remain present inside each wheel's
`.dist-info` directory. AutoDock Vina and Python license files are in
`resources/licenses/`.

3Dmol, React, React DOM, Phosphor Icons React, Tauri, tauri-plugin-dialog, and serde/serde_json license texts/SPDX
provenance are also in `resources/licenses/`. DockStart's own Apache-2.0 text is
packaged separately as `resources/licenses/DockStart-Apache-2.0.txt`.

DockStart's Apache-2.0 license applies to DockStart's own source code only. It
does not replace the licenses of the separately distributed components above.

The complete Windows-target Cargo dependency inventory and production npm
dependency inventory are packaged under `resources/licenses/dependencies/`.
`THIRD_PARTY_DEPENDENCIES.json` records the exact lock-file hashes, package
name/version/license/source, and SHA256 of every copied license or notice file.
MPL-2.0 crates and crates without a source license file also carry their exact
Cargo archive verified against `Cargo.lock`. The release gate validates this
inventory file by file; this hand-written overview is not a substitute for the
machine-readable inventory.
