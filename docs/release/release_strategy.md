# DockStart Release Strategy

> 本文保留 V0.6 阶段的策略演进记录。当前 v0.10.2 双 profile 与强制发布门禁以
> `release_artifact_profile.md`、`windows_packaging.md` 和 `assisted_stable.md` 为准。

本文档定义 DockStart V0.6 起的发布类型、工具链分发边界和许可证策略。V0.6 的目标是让 Windows 打包、安装后首次检查和 GitHub Release 准备可复现，而不是新增 docking 科学功能。

## Release Types

### Developer build

面向开发者源码运行：

- 从仓库源码启动；
- 需要本机已有 Python、Node.js、Rust 和 Tauri 环境；
- 可以使用用户配置的 AutoDock Vina、Python、RDKit 和 Meeko；
- 不承诺开箱即用。

### Lightweight release

面向普通用户的轻量安装包：

- 包含 DockStart 桌面应用本体；
- 包含前端、Tauri shell、Python 后端代码、文档和资源 manifest；
- 不内置完整 Python runtime；
- 不内置 conda 环境；
- 不自动安装 RDKit 或 Meeko；
- 用户仍可在设置页配置外部 Vina 和 Python 工具链。

### Toolchain-assisted release

面向更接近开箱即用的安装包：

- 支持 bundled Vina 检测；
- 支持 bundled Python runtime 解析；
- 提供本地准备脚本和完整性检查；
- 不把第三方源码 zip、conda 环境或 site-packages 提交进 Git；
- 只有在许可证、来源、版本和 sha256 记录齐全时，才考虑把二进制放入本地打包产物。

### Full offline release

未来计划，V0.6 不实现：

- 目标是更完整的离线工具链；
- 需要单独审查 RDKit、Meeko、Python runtime、依赖体积、更新机制和许可证合规；
- 不应在没有审计前直接提交或分发完整 runtime。

## V0.6 Goals

V0.6 只聚焦发布工程：

- 生成 Windows 安装包；
- 支持 bundled Vina 检测和打包前检查；
- 支持 bundled Python runtime 路径解析和 manifest 校验；
- 支持用户配置外部 RDKit/Meeko conda 环境；
- 提供发布检查清单、GitHub Release 模板和 artifact hash 工具；
- 不自动安装 RDKit/Meeko；
- 不提交 `python.exe`、`Lib/`、`DLLs/`、`site-packages/` 或 conda env。

## What Installers Must Not Include

安装包不应包含：

- 真实 docking 数据；
- 用户项目中的 `runs/`、`results/`、`reports/` 输出；
- 大型临时文件；
- 用户本地 `dockstart_settings.json`；
- conda 环境目录；
- 第三方源码 zip；
- 未经许可证确认的二进制工具；
- 开发构建目录，例如 `dist/`、`target/`。

## License Boundaries

- DockStart 本体以仓库 `LICENSE` 为准。
- AutoDock Vina 是第三方工具，可作为 bundled Vina 候选，但必须保留许可证文本、版本、来源和 sha256。
- RDKit 和 Meeko 当前优先作为用户配置 Python 环境中的依赖，不在 V0.6 默认内置。
- Open Babel、MGLTools、PLIP 当前不内置，不进入 V0.6 安装包。
- 任何工具链状态都不能被解释为科学验证或药效判断。
