# AD4Zn 1S63 external acceptance fixture

This directory contains metadata only. It deliberately does **not** contain
the 1S63 receptor, ligand, AD4Zn parameter file, AutoGrid maps, docking
output, or any executable from the upstream AutoDock Vina / ADFR projects.

The acceptance gate reads the scientific inputs and reference outputs from a
user-supplied checkout of the official AutoDock Vina repository:

- repository: `https://github.com/ccsb-scripps/AutoDock-Vina`
- tag: `v1.2.7`
- commit: `8eb40404f4f45608acb3b01427587ac049f27c1f`
- example: `example/docking_with_zinc_metalloproteins`
- upstream license: Apache-2.0
- `data/AD4Zn.dat`: GPL-2.0-or-later, user supplied only

Do not point the verifier at
`example/docking_with_zinc_metalloproteins/data/AD4Zn.dat`. That path is an
upstream symbolic link and appears as a 23-byte link placeholder in some
Windows checkouts. The verifier intentionally reads the real repository-level
`data/AD4Zn.dat`.

Run the external acceptance gate from the DockStart repository root:

```powershell
python scripts/verify_ad4zn_1s63.py `
  --upstream-root C:\path\to\AutoDock-Vina-v1.2.7 `
  --autogrid C:\path\to\ADFRsuite-1.0\bin\autogrid4.exe `
  --vina C:\path\to\vina_1.2.7.exe
```

The caller must provide AutoGrid 4.2.7 or newer. AutoGrid 4.2.6 is rejected
because the behavior of `nbp_r_eps` changed between 4.2.6 and 4.2.7. The Vina
binary must be exactly version 1.2.7 for this pinned acceptance record.
DockStart does not download, install, copy into the repository, or distribute
either external executable.

The verifier does not access the network. It accepts LF and CRLF working-tree
files by validating a canonical line-ending SHA256; when Git metadata is
available it additionally verifies the pinned commit and each required Git
blob. Every DockStart project and generated artifact is created below an
automatically removed temporary directory.

The gate exercises the complete project-level AD4Zn chain:

1. import the official prepared 1S63 receptor and ligand PDBQT files;
2. select and explicitly confirm the supported three-coordinate Zn site;
3. generate a TZ receptor and compare its Zn/TZ coordinates with the official
   v1.2.7 receptor;
4. validate and record the user-provided repository-level `AD4Zn.dat`;
5. generate new maps with the supplied AutoGrid 4.2.7+ executable and compare
   all 64,821 numeric values in each of the A, C, Cl, HD, N, NA, OA,
   electrostatic, and desolvation maps with the pinned official solution
   (`absolute error <= 0.001`; path-bearing headers are not compared);
6. freeze a DockStart run, execute Vina 1.2.7 with AD4 scoring, and check the
   stochastic result against the official score neighborhood;
7. parse `scores.csv` and export the AD4Zn Markdown experiment record.

The Windows Vina 1.2.7 binary used for this acceptance has been observed to
insert 47 NUL padding bytes between `TORSDOF` and `ENDMDL` in each of the nine
models when the ligand input uses CRLF line endings. DockStart may remove only
that recognized padding after preserving the exact raw output. The gate binds
both raw and normalized SHA256 values, requires 9 recognized blocks / 423
removed NUL bytes when this behavior is observed, and rejects every NUL or
other invalid control byte in the published `out.pdbqt`. A platform run that
does not emit the anomaly must record `not_required` instead.

This is an external scientific acceptance gate, not a redistributable example
dataset and not evidence that every zinc coordination environment is
supported. The current protocol remains limited to the reviewed single,
three-coordinate 1S63-style site encoded by DockStart's AD4Zn beta boundary.
