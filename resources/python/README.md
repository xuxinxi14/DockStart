# DockStart Bundled Python Runtime

This directory is reserved for an optional DockStart Full Python runtime.

Current repository state:

- Only this `README.md` is tracked under `resources/python/`.
- Real runtime files such as `python.exe`, `Lib/`, `DLLs/`, `Scripts/`, and
  `site-packages/` are ignored by Git.
- The repository does not include a complete Python runtime.

For Full builds, a maintainer may prepare a local, redistributable Python runtime
and place it here with:

```powershell
python scripts/prepare_bundled_python.py C:\Path\To\Python
```

The script works only with local files. It:

- copies a local Python runtime or `python.exe`;
- calculates `python.exe` sha256;
- runs `python.exe --version` to record the version when possible;
- updates `resources/toolchain_manifest.json`.

It does not download Python, install Python packages, install RDKit, install
Meeko, or add RDKit/Meeko molecule-processing functionality.

Expected optional layout:

```text
resources/python/
├─ python.exe
├─ python*.dll
├─ DLLs/
├─ Lib/
├─ Scripts/
└─ README.md
```

Large runtime files are ignored by Git. Keep this README and
`resources/toolchain_manifest.json` tracked.

DockStart uses bundled Python first for the desktop app's backend runtime:

```text
bundled > configured > current_environment
```

RDKit/Meeko preparation uses the user-configured Python first:

```text
configured > bundled > current_environment
```

The lightweight runtime prepared here is intended to run DockStart's backend.
It does not install RDKit or Meeko. Assisted Mode still expects a separate
configured Python environment with RDKit/Meeko when automatic PDBQT preparation
is required.
