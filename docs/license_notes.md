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
| serde / serde_json | 后台任务事件的结构化序列化 | MIT OR Apache-2.0 | Rust crate，编译进桌面端 | 是 | 否 |
| Python | 后端运行环境 | Python Software Foundation License | v0.10.0 的 Basic/Assisted profile 均随包提供独立 runtime；源码仓库不提交二进制 | 是 | 否 |

## v0.10.0 Basic Stable 分发边界

| 名称 | 用途 | 许可证 | 集成方式 | 是否随包 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| AutoDock Vina 1.2.7 | 执行 docking 任务 | Apache-2.0 | 随应用提供的外部命令行工具，通过 adapter 检测和调用；仍允许用户配置其他路径 | 是 | 否 |
| CPython 3.11.15 | 运行 DockStart Python 后端 | Python Software Foundation License | 精简 runtime，仅含标准库与运行 DLL | 是 | 否 |
| RDKit | Assisted Mode 的配体读取与准备 | BSD-3-Clause | 用户配置的独立 Python 环境 | 否 | 是，仅 Assisted Mode |
| Meeko | Assisted Mode 的 PDBQT 准备 | LGPL-2.1-or-later | 用户配置的独立 Python 环境 | 否 | 是，仅 Assisted Mode |

v0.10.0 Basic Stable 的“开箱即用”仅指已有 receptor/ligand PDBQT 的 Basic Mode。该 profile 不包含
`Lib/site-packages`、Meeko/RDKit 命令行工具或 conda 环境。

## Assisted Stable 分发边界

Assisted Stable 与 Basic Stable 是两个独立发布 profile。Assisted 安装包额外包含普通目录形式的
CPython 3.11 和以下固定 wheel；它们不会被冻结进 `dockstart-desktop.exe`：

| 名称 | 固定版本 | 用途 | 许可证 | 集成方式 | 是否随 Assisted 包 |
| --- | --- | --- | --- | --- | --- |
| Meeko | 0.7.1 | 受体/配体 PDBQT 准备 | `LGPL-2.1`；wheel classifier 标记 `LGPLv2+` | 独立 Python 包；子进程模块入口 | 是 |
| RDKit | 2026.3.3 | SDF/MOL 读取和配体准备 | BSD-3-Clause | 独立 Python 包 | 是 |
| NumPy | 1.26.4 | 科学计算依赖 | BSD-3-Clause 及 wheel 内运行时 notices | 独立 Python wheel | 是 |
| SciPy | 1.17.1 | Meeko 空间计算依赖 | BSD-3-Clause 及 wheel 内运行时 notices | 独立 Python wheel | 是 |
| Gemmi | 0.7.5 | Meeko 受体化学依赖 | MPL-2.0 | 独立 Python wheel | 是 |
| Pillow | 12.2.0 | RDKit wheel 依赖 | MIT-CMU | 独立 Python wheel | 是 |
| tqdm | 4.67.1 | 进度工具 | `MPL-2.0 AND MIT`（保持 wheel 原文） | 独立 Python wheel | 是 |
| tomli | 2.2.1 | TOML 兼容 fallback | MIT | 独立 Python wheel | 是 |
| colorama | 0.4.6 | tqdm 的 Windows 条件依赖 | BSD-3-Clause | 独立 Python wheel | 是 |

合规和可复现边界：

- `resources/assisted/SOURCE_MANIFEST.json` 固定官方 PyPI artifact URL、文件名和 SHA256；
- release builder 只读取 `_external_download/assisted-wheelhouse/`，不会联网或解析浮动依赖；
- 安装包附带 Meeko 0.7.1、Gemmi 0.7.5 和 tqdm 4.67.1 同版本官方 source archive；
- 安装包附带各 wheel 的原始 license/notices；NumPy/SciPy 的数值运行时 notices 不做删减；
- Meeko、Gemmi 和 tqdm 均未被 DockStart 修改；若以后修改，必须重新审查并提供修改后的对应源码；
- preparation Python 优先级为 `configured` → `bundled` → `current_environment`；
- runtime hash 用于发布门禁、缓存键和诊断告警，不得用于阻止用户替换 Meeko；
- Meeko 通过 `python -I -B -m meeko.cli...` 的参数数组执行，不拼接 shell 字符串；
- 第三方许可证不改变 DockStart 自有代码的许可证。

这是一份工程合规记录，不构成法律意见。企业采购、收费闭源发行或修改 LGPL/MPL 组件前仍应进行法律复核。

## 当前可检测或可配置的工具

| 名称 | 用途 | 许可证 | 集成方式 | 是否内置 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| AutoDock Vina | 执行 docking 任务 | Apache-2.0 | Basic 包随应用提供，也可由用户配置外部路径 | 是 | 否；仅替换版本时需要 |
| Meeko | ligand/receptor PDBQT 准备 | LGPL-2.1；wheel classifier 为 LGPLv2+ | Basic 为外部包；Assisted 为独立可替换 bundled 包 | 仅 Assisted | Basic 需要，Assisted 不需要 |
| RDKit | ligand SDF/MOL 读取并配合 Meeko 准备 PDBQT | BSD-3-Clause | Basic 为外部包；Assisted 为独立 bundled 包 | 仅 Assisted | Basic 需要，Assisted 不需要 |
| 3Dmol.js | 结构查看与 Box / docking pose 几何可视化 | BSD-3-Clause | npm 前端依赖 `3dmol`，由 Vite 打包进桌面端，不使用外部 CDN | 是 | 否 |

## Assisted 之后的工具链扩展审查

| 名称 | 当前状态 | 合规要求 |
| --- | --- | --- |
| AutoDock Vina | Basic/Assisted 已随包 | 继续保留许可证文本、版本、来源和修改说明 |
| RDKit | Assisted 已随包 | 继续保留许可证文本、依赖说明和 wheel 来源；升级需重跑门禁 |
| Meeko | Assisted 已随包 | 保持独立可替换、提供对应源码；修改或冻结前重新审查 LGPL |
| Python 运行时 | Basic/Assisted 已随包 | 保留 Python 许可证、版本、来源和 SHA256；仓库不提交 runtime 二进制 |

## Bundled Python Runtime 当前状态

V0.2.3 已完成 bundled Python runtime 的路径解析、manifest 完整性检查和 ToolchainStatusPage 展示，但当前仓库没有提交完整 Python runtime。

当前约束：

- `resources/python/` 当前只提交 `README.md`；
- `resources/python/python.exe`、`Lib/`、`DLLs/`、`Scripts/`、`site-packages/` 等真实 runtime 文件被 `.gitignore` 忽略；
- v0.10.0 Basic 发布使用 `scripts/prepare_basic_release_resources.py` 生成全新的 `.release/basic/` 白名单资源树；
- Basic stage 排除 `Lib/site-packages`、`Scripts`、`__pycache__`、`.pyc` 与 `.pyo`；
- `scripts/prepare_bundled_python.py` 仍只用于准备本地构建输入，不直接定义稳定安装包内容；
- 该脚本不联网、不下载 Python、不安装 Python 包、不安装 RDKit、不安装 Meeko；
- 桌面端后端运行优先级为 `bundled` → `configured` → `current_environment`；
- RDKit/Meeko preparation 工具链优先级为 `configured` → `bundled` → `current_environment`；
- V0.3.1 起 Meeko/RDKit 会做准备能力检测；
- V0.3.2 起 RDKit + Meeko 可用于 ligand SDF/MOL 到 `prepared/ligand.pdbqt` 的自动准备；
- V0.3.3 起 Meeko receptor CLI 可用于 receptor PDB/CIF 到 `prepared/receptor.pdbqt` 的自动准备；
- 当前仓库仍不提交 RDKit、Meeko wheel 或完整 runtime 二进制；Assisted 发布时由固定离线 wheelhouse 装配。

如果后续升级或修改 RDKit/Meeko，需要重新审查：

- 许可证文本和随包告知；
- 源码获取方式；
- 修改说明；
- 包体积和更新机制；
- 与 Python runtime 的版本兼容性；
- 是否允许随 DockStart Assisted 一起分发。

## 本阶段明确不引入

| 名称 | 原因 | 当前处理 |
| --- | --- | --- |
| Open Babel | GPL 许可证与打包策略需要单独确认 | 不作为依赖、不内置、不实现 adapter |
| PLIP | GPLv2 许可证与集成边界需要单独确认 | 不作为依赖、不内置、不实现 adapter |
| MGLTools | 暂不内置，后续如需支持必须先确认许可证和分发方式 | 不作为依赖、不内置、不实现 adapter |

当前已实现 RCSB PDB / PubChem 的 raw 原始结构下载和来源记录，并支持通过用户已有 Python 环境中的 RDKit/Meeko 尝试准备 ligand/receptor PDBQT；V0.4 已开始接入 3Dmol.js 做几何查看和 Box 可视化；仍未实现 MOL2/SMILES 自动准备、Open Babel、PLIP/MGLTools、相互作用分析或药效判断。

## V0.9.4/V0.9.6 Full Bundled Python Packages（历史本地候选）

以下内容只记录 v0.9.4/v0.9.6 阶段的本地 Full 候选实验，不属于 v0.9.7
Basic Stable，也不属于 v0.10.0 Assisted Stable 的固定依赖集，不得用于当前发布声明。

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
