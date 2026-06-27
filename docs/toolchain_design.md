# DockStart Full Toolchain Design

本文档只描述 DockStart Full 的工具链架构方向。本轮不实现新功能，不改变 V0.1 Lite 的业务逻辑。

## 1. 背景

当前 V0.1 Lite 依赖用户已经准备好的 `receptor.pdbqt`、`ligand.pdbqt` 和本机 AutoDock Vina。这个阶段可以验证 docking 最小闭环，但对初学者并不够友好：安装 Vina、配置 PATH、准备 PDBQT、安装 Python 包都会造成较高门槛。

DockStart Full 的目标是把这些环境问题前移到应用分发和工具链管理层，让用户尽量通过一个桌面应用完成分子对接全过程。

## 2. 最终目标

DockStart Full 应具备以下特征：

- 分发简单：安装包包含必要运行资源，减少额外配置。
- 内置工具链：Vina、Python、RDKit、Meeko 等核心组件可由 DockStart 管理。
- 开箱即用：默认流程不要求用户理解 PATH、虚拟环境或 Python 包安装。
- 中文引导：工具缺失、版本不兼容、许可证说明和错误修复建议均使用中文呈现。
- 覆盖全过程：从结构获取、分子准备、对接执行、结果解析到报告导出形成完整闭环。

## 3. 目录设计

建议在桌面应用资源目录中规划：

```text
resources/
├─ tools/
│  └─ vina/
│     ├─ win/
│     ├─ macos/
│     └─ linux/
├─ python/
│  ├─ win/
│  ├─ macos/
│  └─ linux/
└─ licenses/
   ├─ autodock_vina/
   ├─ rdkit/
   ├─ meeko/
   └─ python/
```

说明：

- `resources/tools/vina/`：存放平台相关的 Vina 可执行文件和版本说明。
- `resources/python/`：存放独立 Python 运行时、包环境和锁定版本。
- `resources/licenses/`：存放第三方许可证文本、来源、版本和合规说明。

## 4. 工具解析优先级

工具路径解析顺序固定为：

```text
内置工具 > 用户配置路径 > 系统 PATH
```

含义：

- 内置工具：DockStart Full 随包提供的工具，默认优先使用。
- 用户配置路径：用户在 Settings 或 ToolchainStatusPage 中手动指定的路径，可用于覆盖内置工具。
- 系统 PATH：兜底来源，主要用于开发者或高级用户环境。

每次运行都应把实际使用的工具来源和版本写入 run metadata，保证结果可追踪。

## 5. ToolchainStatusPage

后续新增 `ToolchainStatusPage` 时，建议显示：

| 项目 | 状态 |
| --- | --- |
| Vina | 内置 / 用户配置 / PATH / 缺失 |
| Python | 内置 / 用户配置 / PATH / 缺失 |
| RDKit | 可用 / 缺失 / 版本不兼容 |
| Meeko | 可用 / 缺失 / 版本不兼容 |
| 许可证 | 已包含 / 待补充 / 不可内置 |

页面应提供：

- 当前使用来源；
- 检测到的版本；
- 实际路径；
- 中文错误说明；
- 修复建议；
- 许可证查看入口。

## 6. 许可证策略

当前策略：

- AutoDock Vina：可作为候选内置工具，需保留许可证文本、版本、来源和修改说明。
- RDKit：可作为候选内置组件，需保留许可证文本、依赖说明和构建来源。
- Meeko：可作为候选内置组件，但必须补充 LGPL 合规说明，包括许可证文本、源码获取方式、修改说明和动态/静态链接边界。
- Open Babel：暂不进入核心内置包，后续只作为外部可选集成评估。
- MGLTools：暂不进入核心内置包，后续只作为外部可选集成评估。
- PLIP：暂不进入核心内置包，后续只作为外部可选集成评估。

在许可证和分发边界没有确认前，不应把第三方源码或二进制直接复制进 DockStart 发布包。

## 7. V0.1 Lite 与 Full 的关系

V0.1 Lite：

- 验证项目结构、run、log 解析、CSV 导出和 Markdown 报告；
- 依赖用户已有 PDBQT 和 Vina；
- 不内置工具链；
- 不自动准备分子。

DockStart Full：

- 在 V0.1 的项目和 run 模型基础上扩展；
- 把工具检测升级为工具链管理；
- 把路径配置升级为来源优先级解析；
- 把手动 PDBQT 输入扩展为自动准备流程。

## 8. 不在本阶段实现

本设计文档不代表当前已经实现以下能力：

- 内置 Vina；
- 内置 Python；
- 内置 RDKit / Meeko；
- 自动准备 receptor / ligand；
- PDB / PubChem 下载；
- 专业级 3D 建模检查或相互作用可视化；
- 相互作用分析；
- PDF 报告；
- AI 药效判断。

注：V0.4 已有最小 3Dmol.js ViewerPage，用于项目内结构、Box 和 docking pose 的几何查看；它不属于本工具链设计文档所说的内置工具链能力，也不做相互作用分析或科学验证。
