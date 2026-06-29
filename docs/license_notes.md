# License Notes

本文件记录 DockStart 当前依赖、计划检测的外部工具，以及许可证集成边界。

## 当前脚手架依赖

| 名称 | 用途 | 许可证 | 集成方式 | 是否内置 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| React | 桌面端 UI | MIT | npm 依赖 | 是 | 否 |
| Vite | 前端开发与构建 | MIT | npm 开发依赖 | 是 | 否 |
| Tauri | 桌面应用壳 | Apache-2.0 / MIT | npm CLI + Rust crate | 是 | 需要本机具备 Rust/Tauri 构建环境 |
| tauri-plugin-dialog | 原生文件/目录选择对话框（路径输入的“选择…”按钮） | Apache-2.0 / MIT（Tauri 官方插件） | Rust crate + npm 包，通过 capabilities 授权 `dialog:default` | 是 | 否 |
| Python | 后端运行环境 | Python Software Foundation License | 系统运行时；DockStart Full 可选内置 runtime | 当前仓库不内置 | 是，除非 Full 包已随附 runtime |

## 当前检测但不内置的工具

| 名称 | 用途 | 许可证 | 集成方式 | 是否内置 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| AutoDock Vina | 执行 docking 任务 | Apache-2.0 | 外部命令行工具，通过 adapter 检测和调用 | 否 | 是 |
| Meeko | V0.3.2/V0.3.3 可用于 ligand/receptor PDBQT 准备 | LGPL 合规需确认 | 外部 Python 包，通过 adapter 检测和调用；当前不内置 | 否 | 是 |
| RDKit | V0.3.2 可用于 ligand SDF/MOL 读取并配合 Meeko 准备 PDBQT | BSD | 外部 Python 包，通过 adapter 检测和调用；当前不内置 | 否 | 是 |
| 3Dmol.js | 结构查看与 Box / docking pose 几何可视化 | BSD-3-Clause | npm 前端依赖 `3dmol`，由 Vite 打包进桌面端，不使用外部 CDN | 是 | 否 |

## DockStart Full 候选内置工具链

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
- `scripts/prepare_bundled_python.py` 只复制本地 Python runtime、计算 `python.exe` sha256、读取版本并更新 `resources/toolchain_manifest.json`；
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
