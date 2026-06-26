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
- 明确当前仍不自动转 PDBQT；
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
- 输入校验和错误提示继续推进；
- 许可证、体积、离线包和更新机制审查；
- mock-first 测试方案；
- 第一阶段可以只做设计文档或最小接口，不应直接扩大到复杂分子处理。

注意：

- V0.3 之前，raw 文件仍不能直接运行 Vina；
- V0.3.5 之后，ligand SDF/MOL 和 receptor PDB/CIF 可以尝试自动准备为 PDBQT，并能接回现有 config/run 流程；自动准备过程有独立审计记录；MOL2/SMILES 和复杂结构修复仍需后续阶段实现；
- 不应在缺少许可证审查、测试和用户确认的情况下自动生成 PDBQT；
- Open Babel、PLIP、MGLTools 仍暂不进入核心内置包。

## V0.4: 结构可视化与可视化 Box 设置

计划：

- 3Dmol.js / Mol* 可视化；
- receptor / ligand / pose 查看；
- 可视化 docking box 设置；
- 手动参数与可视化控件同步。

注意：

- 仍应保持数值参数可编辑；
- 可视化结果不应被解释为药效结论。

## V0.5: 相互作用分析

计划：

- ProLIF / PLIP 可选集成；
- 相互作用指纹或相互作用表格；
- 在报告中加入可选相互作用章节。

注意：

- PLIP 等工具许可证需要单独确认；
- Open Babel / MGLTools / PLIP 暂不进入核心内置包；
- 相互作用分析仍不能替代实验验证。

## V0.6: 批量 Docking 与结果管理

计划：

- 批量 docking；
- 多 run 结果管理；
- 项目结果索引；
- 结果排序、筛选和比较；
- 更完善的导出格式。

注意：

- 批量 docking 需要更严格的任务管理和错误恢复；
- 仍不应自动输出药效结论。
