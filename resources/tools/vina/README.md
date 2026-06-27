# DockStart legacy bundled Vina directory

This directory was used by earlier DockStart toolchain experiments for an optional bundled AutoDock Vina executable.

V0.6 uses the shorter preferred path:

```text
resources/vina/vina.exe
```

For compatibility, DockStart may still detect this legacy path if it exists:

```text
resources/tools/vina/vina.exe
```

DockStart resolves Vina in this order:

1. Bundled executable at `resources/vina/vina.exe`
2. Legacy bundled executable at `resources/tools/vina/vina.exe`
3. User-configured Vina path from DockStart settings
4. `vina` or `vina.exe` found on `PATH`

The repository does not require committing `vina.exe`. If a distributor chooses
to ship Vina with DockStart, they must keep the license notes in
`resources/licenses/THIRD_PARTY_NOTICES.md` accurate.

To assemble local files for a Full package without downloading from the network:

```powershell
python scripts/prepare_bundled_vina.py C:\path\to\vina-release-folder --version 1.2.7
```

The V0.6 script copies `vina.exe` and sibling `*.dll` files into `resources/vina/`,
updates `resources/toolchain_manifest.json`, and copies a local Vina license file
to `resources/licenses/AutoDock-Vina_LICENSE.txt` when it can find one.
