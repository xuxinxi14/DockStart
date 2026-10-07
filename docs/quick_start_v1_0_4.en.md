# DockStart v1.0.4 Quick Start

[中文](quick_start_v1_0_4.md) · [English](quick_start_v1_0_4.en.md) · [English README](../README.en.md)

For the Windows 10/11 x64 public trial. The interface is in Chinese, so menu names below match the application. This walkthrough uses toy data to check the software workflow, not to validate docking accuracy.

## 1. Download and check

Open the [v1.0.4 release](https://github.com/xuxinxi14/DockStart/releases/tag/v1.0.4). Both EXE installers are available:

- **Basic**: for already prepared receptor and ligand PDBQT files.
- **Assisted**: for attempting raw receptor PDB/CIF and ligand SDF/MOL/single-molecule MOL2 preparation; it also supports existing PDBQT files.

Check the file in PowerShell, replacing the filename if you chose Basic:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath .\DockStart_1.0.4_Assisted_x64-setup.exe
```

Compare against [the checksums for both installers](release/v1_0_4_SHA256SUMS_2026_10_07.txt). The installers are unsigned. Stop if the digest differs; do not disable security software. Basic and Assisted share an application identity and cannot be installed side by side.

Install and launch DockStart, check that it displays v1.0.4, then open **工具链** (toolchain) to detect the bundled Vina. Ordinary docking needs no separate Python installation. Keep your project folder outside the application installation folder.

## 2. Copy the example

1. On **帮助与入门** (help and onboarding), click **打开示例入口** (open examples), then choose **示例项目（快速体验）** (example projects).
2. Select `basic_pdbqt` and choose your own writable workspace folder.
3. Check the receptor and ligand preparation status. These toy inputs are already PDBQT and need no conversion.

To browse existing results without rerunning Vina, choose `viewer_result`. See [example descriptions, Chinese](demo_projects.md).

## 3. Inspect the docking box and parameters

Open **运行工作台** (run workbench). Keep the ordinary single-ligand, rigid-receptor, global Vina workflow. Inspect the example's saved box, using Å for center coordinates and axis lengths.

Check exhaustiveness, maximum number of output poses, energy range, CPU and random seed. The field question marks provide help. The first run needs no AutoGrid4, maps, AD4Zn or hydrated protocol.

The toy box is specific to the example. For a real structure, determine the search region from a co-crystal ligand, experimental evidence or literature. Geometric centering on the receptor does not predict a binding pocket.

## 4. Run and inspect

1. Save the parameters and resolve all blocking preflight checks.
2. Prepare and start a run; inspect the Vina log and keep its `run_id`.
3. Open **结果** (results) after completion, inspect the score table and switch between available poses.
4. Export the Markdown experiment record and review its input snapshots, configuration, tool versions, logs and `scores.csv`.

`num_modes` is an upper limit; fewer poses need not mean a failed run. RMSD l.b./u.b. is relative to this run's Mode 1, so Mode 1's 0/0 is not evidence of agreement with a crystal structure.

## 5. Move to your own data

Import prepared PDBQT files directly, or use Assisted to attempt raw-structure preparation. Manually inspect protonation, charges, stereochemistry, missing residues, waters, metals and cofactors. Keep file sources, SHA256 values, tool versions, box, parameters and seed in your records.

The [1IEP tutorial, Chinese](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/basic-docking-1iep/) is a separate real-system example with versioned screenshots; its displayed scores are not acceptance targets for a new run.

If blocked, keep the error code, `metadata.json`, `stderr.txt` and `log.txt`. Resolve the cause and create a new run. See [FAQ, Chinese](faq.md), [toolchain repair, Chinese](toolchain_repair_guide.md) or [report an issue](https://github.com/xuxinxi14/DockStart/issues/new/choose) in Chinese or English.

**Docking scores indicate structural binding trends only and cannot replace experimental validation.** Full installer, GUI and scientific acceptance remain incomplete; see [release notes, Chinese](release/v1_0_4_release_notes.md).
