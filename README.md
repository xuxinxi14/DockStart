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

## 当前状态

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

V0.2.3 已支持工具链基础能力：

- 识别可选的 bundled Python runtime 路径：`resources/python/python.exe`；
- 通过 `resources/toolchain_manifest.json` 检查 bundled Python 的版本、来源和 `sha256`；
- 在 `ToolchainStatusPage` 中展示 bundled Python 是否存在、解析路径、版本、`sha256` 和当前 Python 来源；
- Python 解析优先级为 `bundled` → `configured` → `current_environment`；
- Meeko / RDKit 当前只使用解析后的 Python 做 `import` 检测。

V0.2.5 已支持原始结构下载基础层：

- 通过 RCSB PDB ID 下载受体原始结构文件；
- 通过 PubChem CID 下载配体原始 SDF 文件；
- 下载结果保存到当前 DockStart 项目的 `raw/` 目录；
- `project.json` 会记录 `source`、`source_id` 和 `raw_file`；
- raw 文件只记录来源和原始数据，不能直接运行 AutoDock Vina。

V0.2.6 已支持 raw 文件管理增强：

- StructureFetchPage 会显示受体/配体 raw 文件状态、大小、修改时间、绝对路径和记录一致性；
- 下载时 `overwrite` 默认关闭，开启后会显示覆盖警告；
- 可以清除 receptor/ligand 的 raw 记录；
- 清除 raw 记录不会删除 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`；
- 如选择同时删除文件，DockStart 只允许删除项目 `raw/` 目录下的文件。

V0.2.7 已支持结构来源查询增强：

- RCSB PDB 下载支持 `pdb` 和 `cif` 两种 raw 格式；
- PubChem 配体下载继续支持 CID；
- PubChem 配体下载新增名称查询，例如 `aspirin`；
- SMILES 查询当前只返回“暂未支持”的中文结构化提示；
- 不会用 RDKit 解析 SMILES，不会生成 3D，也不会转 PDBQT。

V0.2.8 已增强 raw/prepared 流程引导：

- 首页显示“下载 raw → 手动准备 PDBQT → 导入 prepared PDBQT → 设置参数 → 运行 Vina”的当前推荐流程；
- 创建项目后继续提供“下载原始结构文件”和“直接导入 PDBQT”两个入口；
- PDBQT 导入页强调 raw 文件不能直接运行 Vina；
- StructureFetchPage 下载后提示下一步仍需手动准备并导入 prepared PDBQT；
- ToolchainStatusPage 明确 Meeko/RDKit 当前只是检测，不会自动处理分子。

V0.2.9 已新增手动 PDBQT 准备指南：

- 新增 [docs/manual_pdbqt_preparation.md](docs/manual_pdbqt_preparation.md)；
- 解释 raw 文件和 prepared PDBQT 的区别；
- 说明为什么 Vina 需要 PDBQT；
- 说明 Meeko、AutoDockTools/MGLTools、Open Babel 等外部工具的当前边界；
- 明确 DockStart 当前不保证外部工具生成的 PDBQT 科学正确性。

当前仓库没有提交完整 Python runtime。`resources/python/` 当前只提交 `README.md`，真实 runtime 文件（例如 `python.exe`、`Lib/`、`DLLs/`、`Scripts/`、`site-packages/`）被 `.gitignore` 忽略。

`scripts/prepare_bundled_python.py` 只做本地装配：

- 从本地 Python 目录或 `python.exe` 复制 runtime 文件；
- 计算 `python.exe` 的 `sha256`；
- 运行 `python.exe --version` 读取版本；
- 更新 `resources/toolchain_manifest.json`。

该脚本不联网、不下载 Python、不安装 Python 包、不安装 RDKit、不安装 Meeko。

当前边界：

- 需要用户自己准备 PDBQT 文件；
- 需要用户自己安装或配置 AutoDock Vina；
- 只下载 raw PDB/SDF，不自动准备 receptor / ligand；
- raw 文件状态和记录可以管理，但 raw 仍不能直接运行 Vina；
- 不提交完整 Python runtime；
- 不调用 RDKit 进行配体处理；
- 不调用 Meeko 进行受体/配体准备；
- 不做药效判断。

## 当前暂不支持

当前仍不支持：

- PDB / SDF / MOL2 自动转 PDBQT；
- RDKit 配体处理；
- Meeko 受体 / 配体准备；
- Open Babel；
- PLIP / MGLTools；
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

其中 Python runtime 当前使用：

```text
bundled > configured > current_environment
```

对应架构说明见 [docs/toolchain_design.md](docs/toolchain_design.md) 和 [docs/toolchain_runtime.md](docs/toolchain_runtime.md)。

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
│  ├─ toolchain_runtime.md
│  ├─ user_guide.md
│  ├─ smoke_test.md
│  ├─ faq.md
│  └─ roadmap.md
├─ examples/
│  └─ demo_project/         # 示例项目骨架
├─ CHANGELOG.md
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
3. 可选：下载 RCSB PDB / PubChem CID 原始结构到 `raw/`。
4. 导入已经准备好的 `receptor.pdbqt`。
5. 导入已经准备好的 `ligand.pdbqt`。
6. 设置 docking box。
7. 设置 Vina 参数。
8. 生成 `configs/vina_config.txt`。
9. 准备 run。
10. 执行 Vina。
11. 解析 `log.txt` 并导出 `scores.csv`。
12. 导出 `reports/docking_report.md`。

详细步骤见 [docs/user_guide.md](docs/user_guide.md)，手动验收流程见 [docs/smoke_test.md](docs/smoke_test.md)。
从 raw 文件到 prepared PDBQT 的当前人工流程见 [docs/manual_pdbqt_preparation.md](docs/manual_pdbqt_preparation.md)。

## 输出文件

一次典型 V0.1 run 会生成：

```text
prepared/receptor.pdbqt
prepared/ligand.pdbqt
raw/receptor_1HSG.pdb
raw/ligand_2244.sdf
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
