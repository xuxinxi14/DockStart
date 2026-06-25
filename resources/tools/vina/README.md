# DockStart bundled Vina directory

This directory is reserved for an optional bundled AutoDock Vina executable.

Expected local file:

```text
resources/tools/vina/vina.exe
```

DockStart resolves Vina in this order:

1. Bundled executable at `resources/tools/vina/vina.exe`
2. User-configured Vina path from DockStart settings
3. `vina` or `vina.exe` found on `PATH`

The repository does not require committing `vina.exe`. If a distributor chooses
to ship Vina with DockStart, they must keep the license notes in
`resources/licenses/THIRD_PARTY_NOTICES.md` accurate.

To assemble local files for a Full package without downloading from the network:

```powershell
python scripts/prepare_bundled_vina.py C:\path\to\vina-release-folder --version 1.2.7
```

The script copies `vina.exe` and sibling `*.dll` files into this directory,
updates `resources/toolchain_manifest.json`, and copies a local Vina license file
to `resources/licenses/AutoDock-Vina_LICENSE.txt` when it can find one.
