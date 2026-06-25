# License Notes

本文件记录 DockStart 当前依赖、计划检测的外部工具，以及许可证集成边界。

## 当前脚手架依赖

| 名称 | 用途 | 许可证 | 集成方式 | 是否内置 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| React | 桌面端 UI | MIT | npm 依赖 | 是 | 否 |
| Vite | 前端开发与构建 | MIT | npm 开发依赖 | 是 | 否 |
| Tauri | 桌面应用壳 | Apache-2.0 / MIT | npm CLI + Rust crate | 是 | 需要本机具备 Rust/Tauri 构建环境 |
| Python | 后端运行环境 | Python Software Foundation License | 系统运行时 | 否 | 是 |

## MVP 计划检测但不内置的工具

| 名称 | 用途 | 许可证 | 集成方式 | 是否内置 | 是否需要用户自行安装 |
| --- | --- | --- | --- | --- | --- |
| AutoDock Vina | 执行 docking 任务 | Apache-2.0 | 外部命令行工具，通过 adapter 检测和调用 | 否 | 是 |
| Meeko | 后续受体/配体准备能力 | Apache-2.0 | 外部 Python 包，通过 adapter 检测 | 否 | 是 |
| RDKit | 后续配体读取和检查能力 | BSD | 外部 Python 包，通过 adapter 检测 | 否 | 是 |
| 3Dmol.js | 后续结构可视化 | BSD-3-Clause | 前端依赖，待工具检测页之后再决定接入方式 | 否 | 待定 |

## 本阶段明确不引入

| 名称 | 原因 | 当前处理 |
| --- | --- | --- |
| Open Babel | GPL 许可证与打包策略需要单独确认 | 不作为依赖、不内置、不实现 adapter |
| PLIP | GPLv2 许可证与集成边界需要单独确认 | 不作为依赖、不内置、不实现 adapter |
| MGLTools | 暂不内置，后续如需支持必须先确认许可证和分发方式 | 不作为依赖、不内置、不实现 adapter |
