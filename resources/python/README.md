# DockStart Bundled Python Runtime

This directory is reserved for an optional DockStart Full Python runtime.

DockStart Community does not require this directory to contain `python.exe`.
For Full builds, a maintainer may prepare a local, redistributable Python runtime
and place it here with:

```powershell
python scripts/prepare_bundled_python.py C:\Path\To\Python
```

The script works only with local files. It does not download Python, install
packages, or add RDKit/Meeko functionality.

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
