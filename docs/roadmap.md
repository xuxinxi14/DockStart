# DockStart Roadmap

本文档记录 DockStart 的阶段性路线图。实际优先级会根据用户反馈、许可证边界和维护成本调整。

## V0.1: 本地 PDBQT docking MVP

目标：跑通本地最小闭环。

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

明确不做：

- 自动下载结构；
- 自动分子格式转换；
- 自动药效判断；
- 3D 可视化；
- 相互作用分析。

## V0.2: 结构获取与准备能力

计划：

- PDB / PubChem 下载；
- RDKit / Meeko 准备配体和受体；
- 更完善的错误引导；
- 更清晰的输入结构检查；
- 项目模板和示例数据整理。

注意：

- 仍需确认第三方依赖和许可证边界；
- 不应直接复制第三方源码到 DockStart。

## V0.3: 结构可视化与可视化 box 设置

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
- 相互作用分析仍不能替代实验验证。

## V0.5: 批量 docking 与结果管理

计划：

- 批量 docking；
- 多 run 结果管理；
- 项目结果索引；
- 结果排序、筛选和比较；
- 更完善的导出格式。

注意：

- 批量 docking 需要更严格的任务管理和错误恢复；
- 仍不应自动输出药效结论。
