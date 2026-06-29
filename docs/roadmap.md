# DockStart Roadmap

本文档记录 DockStart 从 V0.1 Lite MVP 走向 DockStart Full 一站式分子对接平台的阶段路线。实际优先级会根据用户反馈、许可证边界、分发体积和维护成本调整。

## 产品方向

DockStart Full 的最终目标：

- 分发简单；
- 内置工具链；
- 开箱即用；
- 中文引导；
- 覆盖分子对接全过程。

当前 V0.1 是 Lite MVP，主要价值是跑通本地 PDBQT docking 闭环。它依赖用户已经准备好的 PDBQT 文件和本机 AutoDock Vina，是阶段性实现，不是最终产品形态。

V0.8 开始把“开箱即用”拆成三种可解释的使用模式：

- Basic Mode：已有 PDBQT，只需要 AutoDock Vina；
- Assisted Mode：从 raw 结构自动准备 PDBQT，需要 Python + RDKit + Meeko；
- Demo Mode：用小型示例体验流程，示例不用于科研结论。

V0.8.1 强化 Basic Mode，确保 raw 下载和自动准备不会阻塞已有 PDBQT 用户完成最低依赖 docking。

V0.8.2 新增 Demo Mode 示例项目，提供小型 Basic / Assisted 玩具数据，用于软件流程演示，不用于科研结论。

V0.8.3 升级首次启动向导，让用户先选择“已有 PDBQT / 只有 raw 文件 / 先看示例”，并基于当前工具链状态显示缺失项和下一步建议。该版本不自动安装工具，也不新增科学功能。

V0.8.4 新增工具链修复建议，把 Vina、Python/RDKit/Meeko 和 Microsoft Store Python 等问题转成可读的手动步骤和可复制命令。该版本仍不自动安装工具、不修改 PATH、不新增科学功能。

V0.8.5 新增安装后自检和本地 Markdown 诊断报告导出，帮助用户判断当前安装能完成 Basic / Assisted / Demo 哪些路径。该版本不上传诊断数据、不自动安装工具，也不改变科学流程。

## V0.1: 本地 PDBQT Docking Lite MVP

目标：跑通最小闭环，证明项目、运行、解析和报告链路可用。

已完成：

- 工具检测；
- Vina / Python 路径配置；
- 创建项目；
- 导入已经准备好的 `receptor.pdbqt` 和 `ligand.pdbqt`；
- 手动设置 docking box；
- 设置 Vina 参数；
- 生成 `vina_config.txt`；
- 准备 run；
- 执行 AutoDock Vina；
- 解析 Vina log；
- 导出 `scores.csv`；
- 前端显示结果表格；
- 导出 Markdown 报告。

明确边界：

- 不内置 Vina；
- 不内置 Python 工具链；
- 不自动下载结构；
- 不自动分子格式转换；
- 不自动准备 receptor / ligand；
- 不自动药效判断；
- 不做 3D 可视化；
- 不做相互作用分析。

## V0.2: DockStart Full 基础路线

V0.2 从“用户自己安装工具”转向“DockStart 管理工具链”，同时为后续结构获取和自动准备留出清晰边界。更多工具链设计见 [toolchain_design.md](toolchain_design.md) 和 [toolchain_runtime.md](toolchain_runtime.md)。

### A. Toolchain Line

这条线只处理工具链资源、路径解析、manifest、许可证和状态展示，不等于已经实现分子准备。

#### V0.2.0: bundled Vina 路径识别，已完成

- 建立 `resources/tools/vina/` 和 `resources/licenses/`；
- 如果存在 `resources/tools/vina/vina.exe`，优先识别为 bundled Vina；
- Vina 解析优先级为 `bundled` → `configured` → `auto`；
- 不强制提交真实 `vina.exe`。

#### V0.2.1: 工具链资源路径与打包兼容，已完成

- 统一开发环境和 Tauri 打包环境中的 `resources/` 解析；
- 支持 Tauri resource dir 下的 packaged resources；
- 确保 manifest、license notes 和工具 README 能进入打包资源。

#### V0.2.2: bundled Vina 装配与许可证检查，已完成

- 新增本地 Vina 装配脚本；
- 记录 bundled Vina 的版本、来源、`sha256` 和许可证状态；
- ToolchainStatusPage 显示 Vina package ready / incomplete / missing；
- 默认不提交真实 Vina 二进制。

#### V0.2.3: bundled Python runtime resolution and integrity check，已完成

- 识别 `resources/python/python.exe`；
- Python 解析优先级为 `bundled` → `configured` → `current_environment`；
- `resources/toolchain_manifest.json` 记录 `bundled_python` 的版本、来源、`sha256` 和准备时间；
- ToolchainStatusPage 显示 bundled Python 是否存在、路径、版本、`sha256` 和当前 Python 来源；
- Meeko / RDKit 检测使用解析后的 Python；V0.2.3 阶段只做 import 检测，V0.3.1 之后增加准备能力检测；
- 当前仓库没有提交完整 Python runtime，`resources/python/` 当前只提交 `README.md`。

#### V0.2.4: 路线校准与工具链文档整理，已完成

- 明确 V0.2.3 是 runtime 解析和完整性检查，不是 RDKit/Meeko 功能接入；
- 明确 `scripts/prepare_bundled_python.py` 只复制本地 Python runtime、计算 `python.exe` sha256、读取版本并更新 manifest；
- 明确该脚本不联网、不安装 Python 包、不安装 RDKit、不安装 Meeko；
- 明确当时仍未实现 PDB/PubChem 下载、PDBQT 自动生成、RDKit/Meeko 分子处理、3D 可视化或药效判断。

#### 后续 Toolchain 方向

- 可选的离线 Python runtime 管理；
- 可选的 RDKit/Meeko 离线包状态检查；
- 更完整的工具链版本锁定、来源记录和升级策略；
- 继续默认不提交大体积二进制 runtime；
- 真正内置 RDKit/Meeko 前必须单独审查许可证、体积、更新机制和分发方式。

### B. Structure Acquisition Line

这条线处理 raw structure 获取和原始文件管理，与 Toolchain line 分开推进。

#### V0.2.5: RCSB PDB / PubChem raw 下载基础层，已完成

- 通过 PDB ID 下载受体相关 raw 结构；
- 通过 PubChem CID 下载配体 raw SDF；
- 保存到项目 `raw/` 目录；
- 记录下载来源、时间和原始文件路径；
- 不自动转 PDBQT；
- 不调用 RDKit、Meeko、Open Babel、PLIP 或 MGLTools。

#### V0.2.6: raw 文件管理增强，已完成

- `get_raw_files_status(project_dir)` 返回 receptor/ligand raw 状态；
- 状态包含 `source`、`source_id`、`raw_file`、`exists`、`size_bytes`、`modified_at`、`absolute_path` 和 `record_consistent`；
- StructureFetchPage 显示 raw 状态卡片、文件大小、修改时间和记录一致性；
- 支持清除 receptor/ligand raw 记录；
- 清除 raw 记录不会删除 prepared PDBQT 文件；
- `delete_file=True` 时只允许删除项目 `raw/` 目录下的文件；
- overwrite 默认关闭，开启时在前端显示覆盖警告。

#### V0.2.7: 结构来源查询增强，已完成

- RCSB PDB 下载支持 `pdb` 和 `cif` 两种 raw 格式；
- PubChem CID 查询保持兼容；
- PubChem name 查询保存为 `raw/ligand_name_{name}.sdf`；
- SMILES 查询返回中文结构化“暂未支持”提示；
- `project.json` 继续记录 `source`、`source_id`、`query_type`、`raw_file` 和 `downloaded_at`；
- 继续保持不自动转 PDBQT、不调用 RDKit/Meeko。

#### V0.2.8: raw/prepared 流程 UI 引导增强，已完成

- 首页显示当前推荐流程；
- ProjectCreatePage 继续提供“下载原始结构文件”和“直接导入 PDBQT”两个入口；
- ImportPdbqtPage 强调 raw 文件和 prepared PDBQT 的区别；
- StructureFetchPage 下载后提示下一步仍需手动准备 PDBQT；
- ToolchainStatusPage 在 V0.2.8 阶段明确 Meeko/RDKit 当时只做 import 检测，不会自动处理分子。

#### V0.2.9: 手动 PDBQT 准备指南，已完成

- 新增 `docs/manual_pdbqt_preparation.md`；
- 写清 raw 文件、prepared PDBQT 和 Vina 输入要求；
- 说明下载 PDB/CIF/SDF 后为什么仍不能直接运行 Vina；
- 说明可选外部工具 Meeko、AutoDockTools/MGLTools 和 Open Babel；
- 记录 Open Babel、MGLTools、PLIP 当前不内置；
- 明确 DockStart 当前不保证外部工具生成的 PDBQT 科学正确性。

#### V0.2.10: smoke test 与 release notes 整理，已完成

- 整理 V0.1 本地 PDBQT 完整流程 smoke test；
- 整理 V0.2 raw 下载流程 smoke test；
- 明确 raw 文件和 prepared 文件的预期产物；
- 明确 raw 文件不等于 prepared PDBQT；
- 明确 V0.2.10 阶段仍不自动转 PDBQT；
- 更新 release notes。

#### 延后：raw → prepared PDBQT 自动准备

- RDKit 配体处理延后；
- Meeko 受体/配体准备延后；
- PDB/SDF/MOL2 自动转 PDBQT 延后；
- Open Babel、PLIP、MGLTools 暂不接入。
- 后续 V0.3 才考虑 RDKit/Meeko 自动准备的设计、测试和许可证审查。

## V0.3: raw → prepared PDBQT 自动准备设计

计划：

- V0.3.0：准备工作流数据模型、状态检查和最小前端入口，已完成；
- V0.3.1：RDKit/Meeko 准备能力检测增强，已完成；只检测 import、版本、基础 SDF 读取和候选 Meeko API/CLI，不执行分子处理；
- V0.3.2：ligand SDF/MOL 自动准备为 `prepared/ligand.pdbqt`，已完成；默认不覆盖已有 prepared ligand，并记录 stdout/stderr/log；
- V0.3.3：receptor PDB/CIF 自动准备为 `prepared/receptor.pdbqt`，已完成；依赖可发现的 Meeko receptor CLI，并记录 stdout/stderr/log；
- V0.3.4：preparation 工作流接入现有 docking 主线，已完成；raw 存在但 prepared 缺失时，config/run 前置检查会提示先准备 PDBQT；preparation 失败时提示查看日志；不改变 Vina config、执行、解析或报告语义；
- V0.3.5：preparation 日志、审计与可复现记录，已完成；每次自动准备写入独立 `preparation/{target}_{NNN}/` 目录和 metadata/stdout/stderr/command/input/output 记录；
- V0.3.6：preparation smoke tests 与文档收尾，已完成；文档说明 raw 下载、自动准备、Box/config/run、结果解析和 Markdown 报告导出的完整路径与科学限制；
- 输入校验和错误提示继续推进；
- 许可证、体积、离线包和更新机制审查；
- mock-first 测试方案；
- 第一阶段可以只做设计文档或最小接口，不应直接扩大到复杂分子处理。

注意：

- V0.3 之前，raw 文件仍不能直接运行 Vina；
- V0.3.5 之后，ligand SDF/MOL 和 receptor PDB/CIF 可以尝试自动准备为 PDBQT，并能接回现有 config/run 流程；自动准备过程有独立审计记录；MOL2/SMILES 和复杂结构修复仍需后续阶段实现；
- 不应在缺少许可证审查、测试和用户确认的情况下自动生成 PDBQT；
- Open Babel、PLIP、MGLTools 仍暂不进入核心内置包。
- V0.4 已完成基础 3D viewer、Box overlay 和 docking pose 查看；相互作用分析、批量 docking、专业级建模检查仍放到后续版本评估。

## V0.4: 结构可视化与可视化 Box 设置

已完成：

- V0.4.0：viewer 后端数据模型与项目内结构文本读取；
- V0.4.1：最小 3Dmol.js ViewerPage，前端依赖通过 npm 管理，不使用 CDN；
- V0.4.2：Box overlay 与 `project.json.box` 保存同步；
- V0.4.3：docking pose mode 查看，并在 `scores.csv` 存在时显示 affinity / RMSD 摘要；
- V0.4.4：viewer 状态接入 workflow status；
- V0.4.5：viewer 文档与 smoke test 收尾；
- V0.4.6：viewer 冻结审计。

注意：

- 仍应保持数值参数可编辑；
- 可视化结果不应被解释为药效结论；
- V0.4 viewer 不做 PLIP/ProLIF、相互作用分析、pocket prediction、自动 Box 推荐或专业建模修复。

## V0.5: 前端工作流整改

已完成：

- V0.5.0：AppShell、Sidebar、Topbar 和共享页面组件基础；
- V0.5.1：ProjectDashboardPage 项目总览；
- V0.5.2：引导式 workflow stepper；
- V0.5.3：统一状态、warning、error、命令结果和科学边界展示组件；
- V0.5.4：StructureFetchPage 与 PreparationPage 信息层级整改；
- V0.5.5：ViewerPage 三栏工作区，整理结构加载、Box 和 pose 查看；
- V0.5.6：Vina config / prepare / execute / result / report 页面统一流程条；
- V0.5.7：HelpPage 与 onboarding；
- V0.5.8：前端工作流冻结审计。

注意：

- V0.5 只整理前端信息架构和中文引导；
- 不改变 Vina config 生成、Vina 执行、score 解析或 Markdown 报告语义；
- 不新增 PLIP/ProLIF、相互作用分析、pocket prediction、药效判断、Open Babel 或 MGLTools；
- 不使用外部 CDN，不提交大型结构文件、真实 docking 输出或 Python runtime。

## V0.6: Windows 打包与发布准备

计划：

- V0.6.0：发布工程结构与打包策略，已完成；
- V0.6.1：bundled Vina 准备与完整性检查，已完成；
- V0.6.2：Python/RDKit/Meeko 工具链分发策略与环境导出，已完成；
- V0.6.3：首次启动与工具链引导，已完成；
- V0.6.4：Windows release build 脚本，已完成；
- V0.6.5：本地安装包构建与验收记录，已完成；
- V0.6.6：GitHub Release 准备，已完成；
- V0.6.7：发布冻结审计，已完成。

注意：

- V0.6 是发布工程线，不新增科学功能；
- V0.6 可以生成本地安装包用于验收，但安装包不得提交进 Git；
- V0.6 不自动安装 RDKit/Meeko，不提交 conda env 或 Python runtime；
- V0.6 不接入 PLIP/ProLIF/Open Babel/MGLTools，不做相互作用分析、pocket prediction 或药效判断。

## V0.7: 批量 Docking 与结果管理

候选方向：

- 批量 docking；
- 多 run 结果管理；
- 项目结果索引；
- 结果排序、筛选和比较；
- 更完善的导出格式。

注意：

- 批量 docking 需要更严格的任务管理和错误恢复；
- 仍不应自动输出药效结论。
## V0.4.0 Viewer 数据模型状态补充

V0.4.0 已完成 viewer 后端数据模型与结构文件读取接口。当前能力只包括：

- 读取项目目录内的 receptor raw、ligand raw、prepared receptor、prepared ligand 和 docking output 文本结构文件；
- 列出和读取 `runs/{run_id}/out.pdbqt` 中的 docking pose 文本；
- 拒绝项目目录外路径，避免路径穿越；
- 对超过 20 MB 的结构文件返回中文结构化错误，避免前端一次性加载过大文本；
- 不调用 RDKit、Meeko 或 AutoDock Vina；
- 不做 PLIP/ProLIF、相互作用分析、pocket prediction 或药效判断。

V0.4.1 已接入最小前端 3Dmol.js ViewerPage，使用 npm 本地依赖，不使用 CDN。V0.4.2 已完成 Box overlay 数据与 `project.json.box` 保存同步。V0.4.3 已完成 docking pose mode 查看和 `scores.csv` 摘要对应。V0.4.4 已把 viewer 状态接入 workflow status，并补充 BoxSetupPage / ResultPage 的最小查看入口。V0.4.5 已完成 viewer 文档与 smoke test 收尾。
