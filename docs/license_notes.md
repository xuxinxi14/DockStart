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
| Python | 后端运行环境 | Python Software Foundation License | v0.10.2 的 Basic/Assisted profile 均随包提供独立 runtime；源码仓库不提交二进制 | 是 | 否 |

## v0.12.0 Basic Stable 分发边界

| 名称 | 用途 | 许可证 | 集成方式 | 是否随包 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| AutoDock Vina 1.2.7 | 执行 docking 任务 | Apache-2.0 | 随应用提供的外部命令行工具，通过 adapter 检测和调用；仍允许用户配置其他路径 | 是 | 否 |
| CPython 3.11.15 | 运行 DockStart Python 后端 | Python Software Foundation License | 精简 runtime，仅含标准库与运行 DLL | 是 | 否 |
| RDKit | Assisted Mode 的配体读取与准备 | BSD-3-Clause | 用户配置的独立 Python 环境 | 否 | 是，仅 Assisted Mode |
| Meeko | Assisted Mode 的 PDBQT 准备 | LGPL-2.1-or-later | 用户配置的独立 Python 环境 | 否 | 是，仅 Assisted Mode |

v0.12.0 Basic Stable 的“开箱即用”仅指已有 receptor/ligand PDBQT 的 Basic Mode。该 profile 不包含
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
| AutoGrid4 4.2.6+ | 生成 AutoDock4 affinity maps；AD4Zn beta 要求 4.2.7 或更高版本 | GNU GPL | 外部命令行工具；仅从用户配置路径或 PATH 检测，通过 adapter 参数数组调用 | 否 | 是，仅 AutoDock4 maps / AD4Zn 协议 |
| `AD4Zn.dat` | AD4Zn 专用 AutoGrid 非键参数 | GPL-2.0-or-later（文件头声明） | 用户从 AutoDock Vina v1.2.7 上游参考自行取得并在项目中选择；DockStart 复制到用户项目，记录本机来源路径、SHA256、许可证 ID、支持配置和上游参考 | 否 | 是，仅 AD4Zn beta |

### v0.12.0 AutoGrid4 结论

- 上游 AutoDock4 下载页将 AutoDock4/AutoGrid4 按 GNU GPL 提供；
- DockStart 不复制、修改或重新分发 AutoGrid4 二进制；
- Basic/Assisted 安装包均不含 `autogrid4.exe`、AutoDock4 安装器或其参数数据；
- 用户自行取得 AutoGrid4 后，可在本机设置中配置路径；
- DockStart 只保存用户运行产生的 GPF、GLG、maps 和可复现 manifest；
- 随包的 AutoDock Vina 1.2.7 保持 Apache-2.0 分发，用于读取 maps 并执行 AD4 评分。

### 当前源码 AD4Zn beta 的参数文件边界

- AD4Zn beta 复用用户自行安装的 AutoGrid4，但硬性要求 AutoGrid4 4.2.7 或更高版本；标准 v0.12.0 AutoDock4 maps 工作流的 4.2.6 基线不等于满足 AD4Zn 门禁；
- `AD4Zn.dat` 文件自身在文件头声明 GPL-2.0-or-later。它与 AutoDock Vina 仓库整体的 Apache-2.0 许可边界不同，不能仅按仓库级许可证处理；
- DockStart 不在 Git、Basic 或 Assisted 资源中内置或重新分发 `AD4Zn.dat`，也不会静默下载。用户应从固定的 AutoDock Vina v1.2.7 根数据路径取得：<https://github.com/ccsb-scripps/AutoDock-Vina/blob/v1.2.7/data/AD4Zn.dat>；
- 用户明确选择文件后，DockStart 会为可复现性把它复制到用户项目、maps 和 run 快照，并记录本机来源路径、文件 SHA256、GPL-2.0-or-later、受支持参数配置和固定上游参考；分享含该副本的项目时，分享者需要自行履行 GPL 再分发义务。这不改变 DockStart 自有 Apache-2.0 代码的许可证；
- 若未来提供应用内下载、离线组件包或随包分发，必须先单独完成 GPL 源码提供、notice、修改说明和再分发方案审查，不能沿用当前“用户提供”结论；
- 以上能力目前只存在于源码工作树，尚未重新打包或进入正式 Release。现有 v0.12.0 Basic/Assisted 安装包不包含 AD4Zn beta，也不包含 `AD4Zn.dat`。

### 当前源码多配体共同对接的依赖边界

- “多配体共同对接（实验性）”复用现有 AutoDock Vina 命令行适配器、PDBQT 输入、项目后端和 3Dmol.js，不引入新的 Python 包、Rust crate、npm 包或外部科研工具；
- 最低运行门槛为 AutoDock Vina 1.2.0；Basic/Assisted 已有的 AutoDock Vina 1.2.7 仍按 Apache-2.0 分发，联合对接不会改变其许可证或分发方式；
- 命令使用一个 `--ligand` 后跟两个用户已准备 PDBQT 路径，不复制第三方算法源码，也不修改 AutoDock Vina 的评分函数；
- 2026-07-28 的官方 5X72 源码级验收只在临时目录取得并使用 AutoDock Vina 上游示例输入，未把这些文件提交到仓库或安装包；如果将来提交或分发任何示例文件，必须像其他科学夹具一样记录精确上游路径、版本、SHA256、用途和许可证，不能因它来自官方示例而省略来源记录；
- 该能力目前只属于 v0.12.2 源码实验性闭环，尚未重新打包或进入正式 Release；现有 v0.12.0 Basic/Assisted 安装包不包含该入口。

### 科学回归夹具

`backend/tests/fixtures/scientific/` 保留 AutoDock Vina v1.2.7 官方示例中的
1FPU 和 BACE_1 最小回归输入。上游仓库以 Apache-2.0 发布；每个夹具目录
同时记录上游路径、用途、文件 SHA256、派生关系和工具版本。夹具只用于源码
测试和人工验收，不会复制进 Basic/Assisted 安装包。

BACE_1 的 SDF 是由外部 Open Babel 2.3.2 从同目录官方 MOL2 机械转换得到，
原始 MOL2 与转换命令模板一并保留。该派生文件不代表 DockStart 引入或分发
Open Babel；DockStart 仍不提供 Open Babel adapter，发布包也不包含其程序或
许可证约束下的二进制。

## Assisted 之后的工具链扩展审查

| 名称 | 当前状态 | 合规要求 |
| --- | --- | --- |
| AutoDock Vina | Basic/Assisted 已随包 | 继续保留许可证文本、版本、来源和修改说明 |
| AutoGrid4 | 外部可选，不随包 | 保持 adapter 边界；若未来考虑分发，必须重新做 GPL 法律与源码提供方案审查 |
| AD4Zn.dat | 用户提供，不随包 | 校验受支持的 v1.2.7 关键参数，记录本机来源、SHA256、许可证 ID 与固定上游参考；任何应用内下载或随包分发方案都需重新审查 GPL-2.0-or-later 边界 |
| RDKit | Assisted 已随包 | 继续保留许可证文本、依赖说明和 wheel 来源；升级需重跑门禁 |
| Meeko | Assisted 已随包 | 保持独立可替换、提供对应源码；修改或冻结前重新审查 LGPL |
| Python 运行时 | Basic/Assisted 已随包 | 保留 Python 许可证、版本、来源和 SHA256；仓库不提交 runtime 二进制 |

## Bundled Python Runtime 当前状态

V0.2.3 已完成 bundled Python runtime 的路径解析、manifest 完整性检查和 ToolchainStatusPage 展示，但当前仓库没有提交完整 Python runtime。

当前约束：

- `resources/python/` 当前只提交 `README.md`；
- `resources/python/python.exe`、`Lib/`、`DLLs/`、`Scripts/`、`site-packages/` 等真实 runtime 文件被 `.gitignore` 忽略；
- v0.12.0 Basic 发布使用 `scripts/prepare_basic_release_resources.py` 生成全新的 `.release/basic/` 白名单资源树；
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
Basic Stable，也不属于 v0.10.2 Assisted Stable 的固定依赖集，不得用于当前发布声明。

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
