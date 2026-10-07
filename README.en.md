[中文](README.md) · [English](README.en.md)

<p align="center"><img src="apps/desktop/public/dockstart-icon.png" width="88" alt="DockStart icon"></p>
<h1 align="center">DockStart</h1>
<p align="center">An open-source, local Windows GUI for AutoDock Vina with a guided Chinese workflow.</p>
<p align="center">
  <a href="https://github.com/xuxinxi14/DockStart/releases/tag/v1.0.4"><img alt="Public trial v1.0.4" src="https://img.shields.io/badge/public_trial-v1.0.4-155f8a"></a>
  <img alt="Windows 10 and 11 x64" src="https://img.shields.io/badge/platform-Windows%2010%20%2F%2011%20x64-1f6feb">
  <a href="LICENSE"><img alt="Apache-2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-2f7d59"></a>
</p>
<p align="center">
  <a href="https://github.com/xuxinxi14/DockStart/releases/tag/v1.0.4">Download v1.0.4</a> ·
  <a href="docs/quick_start_v1_0_4.en.md">Quick Start</a> ·
  <a href="https://xuxinxi14.github.io/DockStart-Docs/">Chinese documentation</a> ·
  <a href="https://github.com/xuxinxi14/DockStart/issues/new/choose">Report an issue</a>
</p>

DockStart brings **PDBQT import or preparation, docking-box setup, Vina execution, pose viewing and experiment records** into one local project. It is intended for students, teaching and users learning protein–ligand docking on Windows.

It runs the existing AutoDock Vina engine without changing its search algorithm or scoring function. Guided setup helps with tool configuration, file formats and errors; users still need to review their structures and choose a justified search region.

> This page describes the **v1.0.4 Windows x64 public trial**, with both Basic and Assisted EXE installers verified on 2026-10-07. Full EXE installation/upgrade/uninstallation, GUI and all scientific release checks remain incomplete. This is not a stable-release claim. The public trial interface is in Chinese; these English instructions retain the Chinese menu names so you can find them.

## Choose a download

| Your starting point | Download | Included tools | Size |
| --- | --- | --- | ---: |
| Prepared receptor and ligand PDBQT files | [Basic EXE](https://github.com/xuxinxi14/DockStart/releases/download/v1.0.4/DockStart_1.0.4_Basic_x64-setup.exe) | Vina and a minimal backend Python runtime | 17.77 MiB |
| Receptor PDB/CIF or ligand SDF/MOL/single-molecule MOL2 requiring preparation | [Assisted EXE](https://github.com/xuxinxi14/DockStart/releases/download/v1.0.4/DockStart_1.0.4_Assisted_x64-setup.exe) | Basic workflow plus CPython 3.11, RDKit, Meeko and preparation dependencies | 70.59 MiB |

Choose **Assisted** if you need raw-structure preparation. It also accepts existing PDBQT files. Ordinary Vina docking needs no separate Python or AutoGrid4 installation. Basic and Assisted share the application identity and cannot be installed side by side; keep projects outside the installation folder and back them up before switching or upgrading.

The installers have no Windows Authenticode signature and may show “unknown publisher.” Verify the download source and SHA256; do not disable security software.

<details>
<summary>Verify installer SHA256</summary>

For example, in PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath .\DockStart_1.0.4_Assisted_x64-setup.exe
```

```text
56c510d40f61333abda0e42a536d70da4ab581889fd23d4a640b59dd649718b5  DockStart_1.0.4_Basic_x64-setup.exe
c495a08184817aa1619116957def9d0d3b30dc9e2d469e638e04565b95253ee2  DockStart_1.0.4_Assisted_x64-setup.exe
```

[Checksums](docs/release/v1_0_4_SHA256SUMS_2026_10_07.txt) · [Verified download inventory](docs/release/v1_0_4_downloads_2026_10_07.json) · [Release notes and verification scope, Chinese](docs/release/v1_0_4_release_notes.md). Stop installation if the digest differs.

</details>

## Quick Start

1. Open **工具链** (toolchain) and check the bundled Vina. Assisted users can also check the preparation tools.
2. In **帮助与入门** (help and onboarding), choose **打开示例入口** (open examples) and copy `basic_pdbqt` into your own writable folder.
3. Open **运行工作台** (run workbench). Use ordinary single-ligand, rigid-receptor, global Vina docking and inspect the example's saved box and parameters.
4. Pass the preflight checks, run Vina, open **结果** (results), inspect poses and scores, and export CSV and a Markdown experiment record.

Follow the [English Quick Start](docs/quick_start_v1_0_4.en.md) for detailed steps. Bundled toy data demonstrate the software workflow. For a real structure, use a justified box and preparation procedure; the [1IEP tutorial, Chinese](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/basic-docking-1iep/) provides a separate example.

## Interface preview

![DockStart v1.0.4 help page with next-step suggestions and offline topics](docs/assets/v1.0.4-help.png)

The actual v1.0.4 help page provides context-dependent next steps, 19 offline topics and error-code lookup.

![DockStart v1.0.3 1IEP teaching example showing poses and scores](docs/assets/1iep-result-v1.0.3.webp)

The results screenshot is from the v1.0.3 1IEP teaching example. It illustrates pose and score viewing; it is not a new v1.0.4 run or evidence of predictive accuracy.

## Features and scientific limits

- Guided workflow, parameter explanations, preflight checks and Chinese error-recovery guidance.
- Three-dimensional structures, docking boxes, output poses and score tables.
- Per-run input snapshots, configuration, tool versions, command arguments, stdout/stderr, logs, timestamps, exit code and relevant SHA256 values.
- Conditional advanced workflows: pose scoring, local optimization, limited receptor flexibility, serial batches, maps, macrocycles and experimental protocols. See [advanced workflows, Chinese](docs/advanced_workflows.md); an available entry point does not imply full acceptance of that protocol in the current installer.

Preparation, docking and project records run locally. Explicit RCSB/PubChem searches and online documentation need network access. Automatic preparation requires manual review of protonation, charges, stereochemistry, missing residues, waters, metals, cofactors and the search region.

RMSD l.b./u.b. in the Vina table is relative to Mode 1 of that run, not an experimental reference. Docking scores cannot establish binding, efficacy, safety or clinical value and should not be compared freely across different scoring functions or systems.

**Docking scores indicate structural binding trends only and cannot replace experimental validation.**

The public trial does not provide SMILES preparation, complex receptor repair, pocket prediction, PLIP/ProLIF interaction analysis, molecular dynamics, PDF reports or AI efficacy assessment. AutoGrid4 is an optional, separately installed external tool; Open Babel, PLIP and MGLTools are not bundled.

## Documentation and development

[Documentation index](docs/README.md) · [User guide, Chinese](docs/user_guide.md) · [FAQ, Chinese](docs/faq.md) · [Toolchain repair, Chinese](docs/toolchain_repair_guide.md).

Development uses Node.js 24.14.1, Rust 1.90.0, the Windows Tauri build prerequisites and Python 3.11+. A clean source checkout needs separately configured or assembled tool resources.

```powershell
git clone https://github.com/xuxinxi14/DockStart.git
cd DockStart\apps\desktop
npm ci
npm run tauri dev
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development, checks and repository layout. The [main-branch source CI reviewed before this documentation update](https://github.com/xuxinxi14/DockStart/actions/runs/37024115435) failed; some tests require a fixed Vina binary, exact fixture bytes and Windows path conditions. Those failures remain to be repaired and verified.

The v1.0.4 functional source reference is [`d314084`](https://github.com/xuxinxi14/DockStart/tree/d314084f246ce96cf0f4b56de888843b7c79c500). The historical installers came from a dirty candidate build; the source reference does not attest to a byte-for-byte rebuild. See the [release record](docs/release/v1_0_4_release_notes.md). The tracked `test/DockStart/` runtime layout is a historical resource, not the current source or a download entry point.

## Feedback, citation and license

Use the [issue forms](https://github.com/xuxinxi14/DockStart/issues/new/choose) for bugs, feature requests and documentation feedback. Chinese and English reports are welcome. Include version, profile, reproduction steps and relevant error codes/logs; remove private paths and research data before posting.

Use **Cite this repository** or [CITATION.cff](CITATION.cff) to cite DockStart, and cite the actual docking engine according to [AutoDock Vina's citation instructions](https://github.com/ccsb-scripps/AutoDock-Vina#citations). DockStart currently has no registered DOI.

DockStart's own code is licensed under [Apache-2.0](LICENSE). Third-party components retain their own licenses; see [license notes](docs/license_notes.md). [Changelog](CHANGELOG.md) · [Roadmap, Chinese](docs/roadmap.md).
