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

## Python Runtime

- Purpose: optional bundled runtime for DockStart's Python backend.
- License: Python Software Foundation License.
- Integration mode: optional bundled runtime under `resources/python/`.
- Default repository state: `python.exe`, `Lib/`, `DLLs/`, and `Scripts/` are
  ignored by Git and prepared only for local release packages.

If a release package includes a Python runtime, keep the Python license file and
runtime source/version metadata with the packaged application.
