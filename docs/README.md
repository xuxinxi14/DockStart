# DockStart 文档目录 / Documentation

[中文 README](../README.md) · [English README](../README.en.md) · [在线中文文档](https://xuxinxi14.github.io/DockStart-Docs/)

公开使用基准为 **v1.0.4 Windows x64 试用版**。截至 2026-10-07，发布页提供 Basic 和 Assisted EXE；开发版本、历史截图、源码级验收与安装包验收分别以对应记录为准。

The public baseline is the v1.0.4 Windows x64 trial. Most detailed documentation is in Chinese; English entry points are listed below. Historical notes are version-specific, and source checks do not establish installer or scientific acceptance.

## 开始使用 / Getting started

| 内容 | 入口 |
| --- | --- |
| 下载、文件身份与已知限制 | [v1.0.4 发布说明](release/v1_0_4_release_notes.md) · [下载核对清单](release/v1_0_4_downloads_2026_10_07.json) |
| 第一次运行 / First run | [中文快速开始](quick_start_v1_0_4.md) · [English Quick Start](quick_start_v1_0_4.en.md) |
| 项目、结构准备、对接箱体、参数与结果 | [用户指南](user_guide.md) |
| 内置示例与真实体系教程 | [示例说明](demo_projects.md) · [在线 1IEP 教程](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/basic-docking-1iep/) |
| 安装、PDBQT、Vina 报错、评分与 RMSD | [FAQ](faq.md) · [工具链修复指南](toolchain_repair_guide.md) |

## 进阶与复现

- [进阶工作流](advanced_workflows.md)：姿势评分、局部优化、柔性、批量、maps、大环和实验性协议的条件。
- [手动 PDBQT 准备](manual_pdbqt_preparation.md)：版本化的外部准备说明；工具与格式支持优先参考当前用户指南。
- [工具链运行机制](toolchain_runtime.md) · [工具链设计](toolchain_design.md)。
- [科学能力计划](scientific_capability_plan.md) · [路线图](roadmap.md)：包含历史阶段记录，不能据此宣称当前安装包全部验收通过。

## 开发、发布与许可证 / Development

- [贡献指南 / Contributing](../CONTRIBUTING.md) · [项目说明](../PROJECT.md) · [Coding Agent 指南](../AGENTS.md)。
- [Windows 打包](release/windows_packaging.md) · [发布检查表](release/release_checklist.md) · [发布模板](release/github_release_template.md)。
- [许可证记录](license_notes.md) · [第三方 notices](../THIRD_PARTY_NOTICES.md) · [软件引用 / Citation](../CITATION.cff)。
- [更新记录](../CHANGELOG.md) · [历史用户指南](history/user_guide_before_v1_0_4_cleanup.md) · [历史 FAQ](history/faq_before_v1_0_4_cleanup.md)。

使用问题、建议和文档改进可通过 [Issue 表单](https://github.com/xuxinxi14/DockStart/issues/new/choose) 用中文或英文提交。

**Docking score 仅供结构结合趋势参考，不能替代实验验证。**
