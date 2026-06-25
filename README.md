# DockStart

DockStart 是一个基于 AutoDock Vina 的第三方开源中文分子对接工作台。

DockStart 的目标是帮助初学者用更清晰的中文流程完成本地 PDBQT docking：导入已经准备好的 receptor/ligand PDBQT 文件，设置 docking box 和 Vina 参数，生成配置文件，运行 AutoDock Vina，解析 docking score，并导出可复现的 Markdown 报告。

## 重要说明

- DockStart 不是 AutoDock Vina 官方项目。
- DockStart 不修改 AutoDock Vina 的 docking 算法、scoring function 或搜索策略。
- DockStart V0.1 只支持用户已经准备好的 `receptor.pdbqt` 和 `ligand.pdbqt`。
- Docking score 仅供结构结合趋势参考，不能替代实验验证，也不能证明真实药效。

## 当前功能

V0.1 MVP 已支持：

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

## 项目结构

```text
DockStart/
├─ apps/
│  └─ desktop/              # Tauri + React 桌面端
├─ backend/
│  ├─ adapters/             # 外部工具检测适配器
│  ├─ dockstart_core/       # 项目、运行、结果解析和报告导出逻辑
│  └─ tests/                # 后端单元测试
├─ docs/
│  ├─ license_notes.md
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
- AutoDock Vina，可通过 PATH 或 DockStart 设置页配置路径。

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
- AutoDock Vina 是第三方工具，需要用户自行安装或配置路径。
- Open Babel / MGLTools / PLIP 暂不内置，也不随 DockStart 打包。
- 第三方依赖和许可证边界详见 [docs/license_notes.md](docs/license_notes.md)。

## 科学免责声明

DockStart 输出的 docking score 只表示特定输入结构、box、参数和 AutoDock Vina 版本下的计算结果。它不能直接证明真实结合能力、药效、安全性或临床价值。任何候选分子判断都需要实验验证和更完整的计算/实验流程支持。
