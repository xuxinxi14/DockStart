# DockStart

DockStart 是一个基于 AutoDock Vina 的第三方开源中文分子对接工作台。

DockStart 的最终目标不是做一个“外部工具调用器”，而是成为开箱即用的一站式分子对接平台：分发简单、内置工具链、中文引导清晰，尽可能覆盖从结构获取、分子准备、box 设置、Vina 对接、结果解析到报告导出的完整流程。

## 重要说明

- DockStart 不是 AutoDock Vina 官方项目。
- DockStart 不修改 AutoDock Vina 的 docking 算法、scoring function 或搜索策略。
- DockStart 当前 V0.1 是 Lite MVP：依赖用户已有的 `receptor.pdbqt`、`ligand.pdbqt` 和 AutoDock Vina。
- V0.1 Lite 只是阶段性实现，不代表 DockStart 的最终形态。
- Docking score 仅供结构结合趋势参考，不能替代实验验证，也不能证明真实药效。

## 产品目标

DockStart Full 的长期方向：

- 分发简单：用户下载桌面应用后尽量少配置或零配置。
- 内置工具链：优先随应用提供 Vina、Python、RDKit、Meeko 等合规组件。
- 开箱即用：默认优先使用内置工具，减少 PATH、环境变量和 Python 包安装问题。
- 中文引导：关键参数、错误、路径和报告都提供面向初学者的中文说明。
- 覆盖全过程：逐步覆盖结构获取、分子准备、对接执行、结果解析、报告导出和结果管理。

## 当前 V0.1 Lite MVP

V0.1 Lite 已支持：

- 工具检测；
- Vina / Python 路径配置；
- 创建 DockStart 项目；
- 导入 `receptor.pdbqt` / `ligand.pdbqt`；
- 设置 docking box；
- 设置 Vina 参数；
- 生成 `configs/vina_config.txt`；
- 准备 run；
- 执行 AutoDock Vina；
- 解析 `runs/{run_id}/log.txt`；
- 导出 `scores.csv`；
- 导出 Markdown 报告。

V0.1 Lite 的边界：

- 需要用户自己准备 PDBQT 文件；
- 需要用户自己安装或配置 AutoDock Vina；
- 不自动下载 PDB / PubChem；
- 不自动准备 receptor / ligand；
- 不提供内置 Python 工具链；
- 不做药效判断。

## 暂不支持

V0.1 暂不支持：

- PDB / PubChem 下载；
- PDB / SDF / MOL2 自动转 PDBQT；
- Meeko / RDKit 自动处理；
- 3D 可视化选框；
- PLIP / ProLIF 相互作用分析；
- 分子动力学；
- PDF 报告；
- AI 药效判断。

## DockStart Full 工具链方向

后续 Full 版本计划采用分层工具链：

```text
resources/
├─ tools/
│  └─ vina/
├─ python/
└─ licenses/
```

工具解析优先级：

```text
内置工具 > 用户配置路径 > 系统 PATH
```

对应架构说明见 [docs/toolchain_design.md](docs/toolchain_design.md)。

## 项目结构

```text
DockStart/
├─ apps/
│  └─ desktop/              # Tauri + React 桌面端
├─ backend/
│  ├─ adapters/             # 工具检测和调用适配器
│  ├─ dockstart_core/       # 项目、运行、结果解析和报告导出逻辑
│  └─ tests/                # 后端单元测试
├─ docs/
│  ├─ license_notes.md
│  ├─ toolchain_design.md
│  ├─ user_guide.md
│  ├─ smoke_test.md
│  ├─ faq.md
│  └─ roadmap.md
├─ examples/
│  └─ demo_project/         # 示例项目骨架
├─ PROJECT.md
└─ CLAUDE.md
```

## 开发环境

建议准备：

- Python 3.11+；
- Node.js 和 npm；
- Rust 工具链；
- Tauri 所需系统依赖；
- AutoDock Vina：V0.1 Lite 需要用户通过 PATH 或 DockStart 设置页配置路径。

## 开发运行

后端测试：

```powershell
python -m unittest discover -s backend/tests
```

前端构建：

```powershell
cd apps/desktop
npm run build
```

Tauri 检查：

```powershell
cd E:\DockStart
cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml
```

Vite 前端开发服务：

```powershell
cd apps/desktop
npm run dev
```

Tauri 桌面端开发启动：

```powershell
cd apps/desktop
npm run tauri dev
```

## V0.1 基本流程

1. 配置 AutoDock Vina 路径。
2. 创建 DockStart 项目。
3. 导入已经准备好的 `receptor.pdbqt`。
4. 导入已经准备好的 `ligand.pdbqt`。
5. 设置 docking box。
6. 设置 Vina 参数。
7. 生成 `configs/vina_config.txt`。
8. 准备 run。
9. 执行 Vina。
10. 解析 `log.txt` 并导出 `scores.csv`。
11. 导出 `reports/docking_report.md`。

详细步骤见 [docs/user_guide.md](docs/user_guide.md)，手动验收流程见 [docs/smoke_test.md](docs/smoke_test.md)。

## 输出文件

一次典型 V0.1 run 会生成：

```text
prepared/receptor.pdbqt
prepared/ligand.pdbqt
configs/vina_config.txt
runs/run_001/metadata.json
runs/run_001/out.pdbqt
runs/run_001/log.txt
runs/run_001/scores.csv
results/scores.csv
runs/run_001/docking_report.md
reports/docking_report.md
```

## 许可证与第三方工具

- DockStart 本体计划采用 Apache License 2.0；正式授权以仓库 `LICENSE` 文件为准。
- AutoDock Vina 许可证允许作为 DockStart Full 的候选内置工具，但需要保留许可证文本和来源说明。
- RDKit 可作为候选内置组件，但需要随包保留许可证和依赖说明。
- Meeko 可作为候选内置组件，但需要补充 LGPL 合规说明，包括许可证文本、源码获取方式和修改说明。
- Open Babel / MGLTools / PLIP 暂不进入核心内置包，可作为后续外部可选集成评估。
- 第三方依赖和许可证边界详见 [docs/license_notes.md](docs/license_notes.md)。

## 科学免责声明

DockStart 输出的 docking score 只表示特定输入结构、box、参数和 AutoDock Vina 版本下的计算结果。它不能直接证明真实结合能力、药效、安全性或临床价值。任何候选分子判断都需要实验验证和更完整的计算/实验流程支持。
