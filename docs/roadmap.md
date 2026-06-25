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

## V0.2: DockStart Full 工具链基础

V0.2 的重点从“用户自己安装工具”转向“DockStart 管理工具链”。更多设计见 [toolchain_design.md](toolchain_design.md)。

### V0.2.0: 内置 Vina

- 在 `resources/tools/vina/` 中提供平台对应的 Vina 可执行文件；
- 启动时优先检测内置 Vina；
- 保留用户自定义 Vina 路径作为覆盖选项；
- 许可证文本放入 `resources/licenses/`。

### V0.2.1: Toolchain 管理

- 新增 `ToolchainStatusPage`；
- 显示内置工具、用户配置路径和 PATH 检测结果；
- 明确工具来源：内置、用户配置、PATH、缺失；
- 统一错误提示和修复建议。

### V0.2.2: 内置 Python 环境

- 评估 `resources/python/` 中的独立 Python 运行时；
- 避免依赖用户系统 Python；
- 建立 Python 包版本锁定和健康检查；
- 记录 Python 和包版本到 run metadata。

### V0.2.3: 内置 RDKit / Meeko 检测

- 在内置 Python 环境中检测 RDKit；
- 在内置 Python 环境中检测 Meeko；
- 显示许可证和版本信息；
- Meeko 需要补充 LGPL 合规说明。

### V0.2.4: 配体自动准备

- 从 SDF / MOL2 / PDB 等输入准备 `ligand.pdbqt`；
- 记录输入文件、处理参数、输出文件和日志；
- 失败时给出中文可操作错误。

### V0.2.5: 受体自动准备

- 从 PDB 等输入准备 `receptor.pdbqt`；
- 明确氢、缺失原子、残基、金属离子等处理限制；
- 记录处理日志和可复现参数。

## V0.3: 结构可视化与可视化 Box 设置

计划：

- 3Dmol.js / Mol* 可视化；
- receptor / ligand / pose 查看；
- 可视化 docking box 设置；
- 手动参数与可视化控件同步。

注意：

- 仍应保持数值参数可编辑；
- 可视化结果不应被解释为药效结论。

## V0.4: 相互作用分析

计划：

- ProLIF / PLIP 可选集成；
- 相互作用指纹或相互作用表格；
- 在报告中加入可选相互作用章节。

注意：

- PLIP 等工具许可证需要单独确认；
- Open Babel / MGLTools / PLIP 暂不进入核心内置包；
- 相互作用分析仍不能替代实验验证。

## V0.5: 批量 Docking 与结果管理

计划：

- 批量 docking；
- 多 run 结果管理；
- 项目结果索引；
- 结果排序、筛选和比较；
- 更完善的导出格式。

注意：

- 批量 docking 需要更严格的任务管理和错误恢复；
- 仍不应自动输出药效结论。
