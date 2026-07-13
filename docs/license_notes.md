# License Notes

本文件记录 DockStart 当前依赖、计划检测的外部工具，以及许可证集成边界。

## 当前脚手架依赖

| 名称 | 用途 | 许可证 | 集成方式 | 是否内置 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| React | 桌面端 UI | MIT | npm 依赖 | 是 | 否 |
| Phosphor Icons React 2.1.10 | 桌面端导航、状态与操作图标 | MIT | npm 依赖 `@phosphor-icons/react`，由 Vite 按需打包 | 是 | 否 |
| Vite | 前端开发与构建 | MIT | npm 开发依赖 | 是 | 否 |
| Tauri | 桌面应用壳 | Apache-2.0 / MIT | npm CLI + Rust crate | 是 | 需要本机具备 Rust/Tauri 构建环境 |
| tauri-plugin-dialog | 原生文件/目录选择对话框（路径输入的“选择…”按钮） | Apache-2.0 / MIT（Tauri 官方插件） | Rust crate + npm 包，通过 capabilities 授权 `dialog:default` | 是 | 否 |
| Python | 后端运行环境 | Python Software Foundation License | v0.9.7 Basic Stable 随包提供精简 runtime；源码仓库不提交二进制 | Basic 安装包内置 | 否 |

## v0.9.7 Basic Stable 分发边界

| 名称 | 用途 | 许可证 | 集成方式 | 是否随包 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| AutoDock Vina 1.2.7 | 执行 docking 任务 | Apache-2.0 | 随应用提供的外部命令行工具，通过 adapter 检测和调用；仍允许用户配置其他路径 | 是 | 否 |
| CPython 3.11.15 | 运行 DockStart Python 后端 | Python Software Foundation License | 精简 runtime，仅含标准库与运行 DLL | 是 | 否 |
| RDKit | Assisted Mode 的配体读取与准备 | BSD-3-Clause | 用户配置的独立 Python 环境 | 否 | 是，仅 Assisted Mode |
| Meeko | Assisted Mode 的 PDBQT 准备 | LGPL-2.1-or-later | 用户配置的独立 Python 环境 | 否 | 是，仅 Assisted Mode |

v0.9.7 的“开箱即用”仅指已有 receptor/ligand PDBQT 的 Basic Mode。安装包不包含
`Lib/site-packages`、Meeko/RDKit 命令行工具或 conda 环境。

## 当前可检测或可配置的工具

| 名称 | 用途 | 许可证 | 集成方式 | 是否内置 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| AutoDock Vina | 执行 docking 任务 | Apache-2.0 | Basic 包随应用提供，也可由用户配置外部路径 | 是 | 否；仅替换版本时需要 |
| Meeko | V0.3.2/V0.3.3 可用于 ligand/receptor PDBQT 准备 | LGPL 合规需确认 | 外部 Python 包，通过 adapter 检测和调用；当前不内置 | 否 | 是 |
| RDKit | V0.3.2 可用于 ligand SDF/MOL 读取并配合 Meeko 准备 PDBQT | BSD | 外部 Python 包，通过 adapter 检测和调用；当前不内置 | 否 | 是 |
| 3Dmol.js | 结构查看与 Box / docking pose 几何可视化 | BSD-3-Clause | npm 前端依赖 `3dmol`，由 Vite 打包进桌面端，不使用外部 CDN | 是 | 否 |

## DockStart Full 候选内置工具链（后续阶段）

| 名称 | 是否可作为候选内置 | 合规要求 |
| --- | --- | --- |
| AutoDock Vina | 是 | 随包保留许可证文本、版本、来源和修改说明 |
| RDKit | 是 | 保留许可证文本、依赖说明和构建来源 |
| Meeko | 是 | 补充 LGPL 合规说明，包括许可证文本、源码获取方式、修改说明和链接边界 |
| Python 运行时 | 是，作为 Full 版候选内置 runtime | 保留 Python 许可证、版本、来源、sha256 和打包来源说明；默认不提交完整 runtime 或 site-packages |

## Bundled Python Runtime 当前状态

V0.2.3 已完成 bundled Python runtime 的路径解析、manifest 完整性检查和 ToolchainStatusPage 展示，但当前仓库没有提交完整 Python runtime。

当前约束：

- `resources/python/` 当前只提交 `README.md`；
- `resources/python/python.exe`、`Lib/`、`DLLs/`、`Scripts/`、`site-packages/` 等真实 runtime 文件被 `.gitignore` 忽略；
- v0.9.7 发布使用 `scripts/prepare_basic_release_resources.py` 生成全新的 `.release/basic/` 白名单资源树；
- Basic stage 排除 `Lib/site-packages`、`Scripts`、`__pycache__`、`.pyc` 与 `.pyo`；
- `scripts/prepare_bundled_python.py` 仍只用于准备本地构建输入，不直接定义稳定安装包内容；
- 该脚本不联网、不下载 Python、不安装 Python 包、不安装 RDKit、不安装 Meeko；
- 桌面端后端运行优先级为 `bundled` → `configured` → `current_environment`；
- RDKit/Meeko preparation 工具链优先级为 `configured` → `bundled` → `current_environment`；
- V0.3.1 起 Meeko/RDKit 会做准备能力检测；
- V0.3.2 起 RDKit + Meeko 可用于 ligand SDF/MOL 到 `prepared/ligand.pdbqt` 的自动准备；
- V0.3.3 起 Meeko receptor CLI 可用于 receptor PDB/CIF 到 `prepared/receptor.pdbqt` 的自动准备；
- 当前仓库仍不内置 RDKit、Meeko 或完整 conda environment。

如果后续真正内置 RDKit/Meeko，需要单独审查：

- 许可证文本和随包告知；
- 源码获取方式；
- 修改说明；
- 包体积和更新机制；
- 与 Python runtime 的版本兼容性；
- 是否允许随 DockStart Full 一起分发。

## 本阶段明确不引入

| 名称 | 原因 | 当前处理 |
| --- | --- | --- |
| Open Babel | GPL 许可证与打包策略需要单独确认 | 不作为依赖、不内置、不实现 adapter |
| PLIP | GPLv2 许可证与集成边界需要单独确认 | 不作为依赖、不内置、不实现 adapter |
| MGLTools | 暂不内置，后续如需支持必须先确认许可证和分发方式 | 不作为依赖、不内置、不实现 adapter |

当前已实现 RCSB PDB / PubChem 的 raw 原始结构下载和来源记录，并支持通过用户已有 Python 环境中的 RDKit/Meeko 尝试准备 ligand/receptor PDBQT；V0.4 已开始接入 3Dmol.js 做几何查看和 Box 可视化；仍未实现 MOL2/SMILES 自动准备、Open Babel、PLIP/MGLTools、相互作用分析或药效判断。

## V0.9.4 Full Bundled Python Packages（历史本地候选）

以下内容只记录 v0.9.4/v0.9.6 阶段的本地 Full 候选实验，不属于 v0.9.7
Basic Stable 的安装包能力，也不得用于当前发布声明。

This local Full packaging profile can include Python packages inside
`resources/python/` so DockStart can run Assisted Mode without a separate conda
configuration. These package files remain ignored by Git and are only bundled
into local release artifacts.

| Name | Purpose | License | Integration | Bundled in Full package |
| --- | --- | --- | --- | --- |
| RDKit 2026.3.3 | Ligand structure reading and preparation support | BSD-3-Clause | Python package in bundled runtime | Yes, local package artifact only |
| Meeko 0.7.1 | Receptor/ligand PDBQT preparation | LGPL-2.1-or-later | Python package and CLI in bundled runtime | Yes, local package artifact only |
| NumPy 1.26.4 | Scientific Python dependency compatible with RDKit/Meeko/SciPy/ProDy | BSD-3-Clause | Python wheel dependency | Yes, local package artifact only |
| SciPy 1.17.1 | Meeko dependency | BSD-3-Clause plus bundled numerical runtime notices | Python wheel dependency | Yes, local package artifact only |
| Pillow 12.2.0 | RDKit wheel dependency | MIT-CMU | Python wheel dependency | Yes, local package artifact only |
| Gemmi 0.7.5 | Meeko dependency | MPL-2.0 | Python wheel dependency | Yes, local package artifact only |
| ProDy 2.4.1 | Meeko ProDy reader support for receptor preparation | MIT | Python package copied from local verified conda toolchain into bundled runtime | Yes, local package artifact only |
| Biopython 1.87 | ProDy dependency | LicenseRef-Biopython-License-Agreement | Python wheel dependency | Yes, local package artifact only |
| pyparsing 3.3.2 | ProDy dependency | MIT | Python wheel dependency | Yes, local package artifact only |

Release builders must keep package metadata and license files with the bundled
runtime, and must preserve `resources/licenses/THIRD_PARTY_NOTICES.md`.
DockStart still does not bundle or call PLIP, ProLIF, Open Babel, or MGLTools,
and it still does not perform interaction analysis, pocket prediction, drug
efficacy judgment, or AutoDock Vina algorithm changes.
