# PROJECT.md

# DockStart
 项目说明

## 1. 项目定位

DockStart 是一个基于 AutoDock Vina 的第三方开源中文分子对接工作台，目标是帮助初学者完成受体/配体准备、对接箱体设置、AutoDock Vina 参数生成、任务运行、结果解析和报告导出。

本项目不是新的分子对接算法，也不修改 AutoDock Vina 的打分函数或搜索算法。项目重点是：

* 降低 AutoDock Vina 使用门槛；
* 提供中文流程引导；
* 减少文件格式、路径、参数设置错误；
* 记录每一步输入、输出、参数和日志；
* 提高课程设计、本科毕设、教学演示和初级科研预实验的可复现性。

## 2. 核心原则

### 2.1 不改核心算法

第一阶段不得修改 AutoDock Vina 核心算法，不得修改 scoring function，不得声称本项目提高了对接准确率。

DockStart 只作为图形化前端、流程管理器、参数生成器、运行器和结果解析器。

### 2.2 优先跑通最小闭环

第一阶段目标是跑通：

```text
导入 receptor.pdbqt
导入 ligand.pdbqt
设置 docking box
生成 vina_config.txt
调用 vina
解析 log
显示 affinity 表格
导出 Markdown 报告
```

不要一开始就加入批量虚拟筛选、分子动力学、AI 药效预测、自动论文生成等复杂功能。

### 2.3 工具适配器模式

所有外部软件都必须通过 adapter 调用，不允许在业务逻辑中硬编码具体命令。

推荐适配器：

```text
VinaAdapter
MeekoAdapter
RDKitAdapter
OpenBabelAdapter
PubChemAdapter
PDBAdapter
ViewerAdapter
```

每个 adapter 至少提供：

* detect()
* version()
* validate_input()
* run()
* parse_output()

### 2.4 外部工具谨慎集成

AutoDock Vina、Meeko、RDKit 可作为核心优先支持对象。

Open Babel、PLIP、MGLTools 等工具许可证或依赖更复杂，第一阶段只能作为外部可选后端，不得默认内置打包。

### 2.5 中文新手友好

所有关键参数必须提供中文解释。

例如：

* exhaustiveness：搜索彻底程度，越高越慢；
* num_modes：输出构象数量；
* center_x/y/z：对接箱体中心坐标；
* size_x/y/z：对接箱体尺寸，单位为 Å；
* seed：随机种子，用于复现实验。

错误提示必须翻译成人能看懂的中文，不允许只把英文 stderr 原样丢给用户。

## 3. 技术栈

### 3.1 桌面端

推荐：

```text
Tauri + React + Vite + TypeScript
```

原因：

* 可以使用 Web 技术做现代 UI；
* 可以调用本地命令行工具；
* 打包体积相对 Electron 更小；
* 适合桌面科研工具。

### 3.2 后端

推荐：

```text
Python
```

用途：

* 调用 AutoDock Vina；
* 调用 Meeko；
* 调用 RDKit；
* 处理 PDB/SDF/MOL2/PDBQT 文件；
* 解析 log；
* 生成报告；
* 管理项目文件。

### 3.3 可视化

第一阶段优先使用：

```text
3Dmol.js
```

用途：

* 显示蛋白结构；
* 显示配体；
* 显示 docking box；
* 预览 docking pose。

后续可以评估 Mol* 作为替代或高级查看器。

## 4. 项目文件结构

建议结构：

```text
dockstart/
├─ PROJECT.md
├─ CLAUDE.md
├─ README.md
├─ LICENSE
├─ apps/
│  └─ desktop/
├─ backend/
│  ├─ dockstart_core/
│  ├─ adapters/
│  ├─ workflows/
│  └─ tests/
├─ examples/
│  └─ demo_project/
├─ docs/
│  ├─ user_guide.md
│  ├─ developer_guide.md
│  └─ license_notes.md
└─ tools/
   └─ external/
```

## 5. Docking 项目结构

每个用户项目必须保存为独立文件夹：

```text
project_name/
├─ raw/
├─ prepared/
├─ configs/
├─ runs/
├─ results/
└─ reports/
```

示例：

```text
demo_project/
├─ raw/
│  ├─ receptor_original.pdb
│  └─ ligand_original.sdf
├─ prepared/
│  ├─ receptor.pdbqt
│  └─ ligand.pdbqt
├─ configs/
│  └─ vina_config.txt
├─ runs/
│  └─ run_001/
│     ├─ out.pdbqt
│     ├─ log.txt
│     └─ metadata.json
├─ results/
│  └─ scores.csv
└─ reports/
   └─ docking_report.md
```

## 6. 第一阶段功能范围

第一阶段只做 MVP。

### 6.1 工具检测

实现工具检测页：

* AutoDock Vina 是否存在；
* Vina 版本；
* Python 是否存在；
* Meeko 是否可用；
* RDKit 是否可用；
* 3Dmol.js 是否可加载。

检测结果显示：

```text
已检测 / 未检测 / 版本不兼容 / 路径错误
```

### 6.2 PDBQT 导入

支持用户导入：

* receptor.pdbqt
* ligand.pdbqt

暂时不强制做自动格式转换。

### 6.3 Box 设置

支持两种模式：

1. 手动输入：

   * center_x
   * center_y
   * center_z
   * size_x
   * size_y
   * size_z

2. 3D 可视化辅助：

   * 显示蛋白；
   * 显示透明 box；
   * 用户拖动或输入参数；
   * 参数实时同步。

### 6.4 参数设置

支持：

* exhaustiveness
* num_modes
* energy_range
* cpu
* seed

必须有中文解释和推荐值。

### 6.5 生成配置文件

自动生成：

```text
vina_config.txt
```

示例：

```text
receptor = prepared/receptor.pdbqt
ligand = prepared/ligand.pdbqt

center_x = 0
center_y = 0
center_z = 0

size_x = 20
size_y = 20
size_z = 20

exhaustiveness = 8
num_modes = 9
energy_range = 4
cpu = 8
seed = 12345
```

### 6.6 运行 Vina

通过命令行调用：

```text
vina --config configs/vina_config.txt --out runs/run_001/out.pdbqt --log runs/run_001/log.txt
```

运行时需要：

* 显示实时日志；
* 支持取消任务；
* 保存 stdout/stderr；
* 记录开始时间、结束时间、工具版本、参数。

### 6.7 解析结果

解析 Vina log 中的结果表：

```text
mode | affinity | dist from best mode
```

输出：

```text
results/scores.csv
```

UI 显示：

* Pose 编号；
* affinity；
* RMSD lower bound；
* RMSD upper bound；
* 输出文件路径。

### 6.8 报告导出

导出 Markdown 报告：

```text
reports/docking_report.md
```

报告必须包含：

* 项目名称；
* 受体文件；
* 配体文件；
* Vina 版本；
* 配置参数；
* 运行时间；
* docking score 表格；
* 注意事项；
* 免责声明。

## 7. 第二阶段功能范围

第二阶段再加入：

* PDB ID 下载蛋白；
* PubChem 获取配体；
* RDKit 读取和检查配体；
* Meeko 生成 ligand.pdbqt；
* Meeko 生成 receptor.pdbqt；
* Open Babel 可选格式转换；
* pose 可视化；
* 结果导出为 SDF；
* 项目模板和示例数据。

## 8. 第三阶段功能范围

第三阶段再考虑：

* ProLIF 相互作用指纹；
* PLIP 外部工具适配；
* fpocket 口袋推荐；
* 批量配体 docking；
* SQLite 结果数据库；
* 多次重复运行；
* 参数对比；
* 教学模式。

## 9. 不允许第一阶段实现的功能

第一阶段不要做：

* 修改 Vina 算法；
* 自称预测药效；
* AI 自动生成论文结论；
* 分子动力学模拟；
* 大规模虚拟筛选；
* 自动判断药物是否有效；
* 自动下结论“该分子可作为候选药物”。

## 10. 科学免责声明

DockStart 输出的 docking score 只表示特定模型、特定参数和特定结构下的计算预测结果，不能直接证明真实结合能力、药效、安全性或临床价值。

软件必须在报告中加入免责声明：

```text
Docking score 仅供结构结合趋势参考，不能替代实验验证。
```

## 11. 许可证策略

本项目自己的代码建议使用 Apache License 2.0。

第三方工具不得混淆许可证：

* AutoDock Vina：Apache 2.0；
* RDKit：BSD；
* 3Dmol.js：BSD-3-Clause；
* React/Vite：MIT；
* Open Babel：GPL，作为外部可选工具；
* PLIP：GPLv2，作为外部可选工具；
* MGLTools：暂不内置。

项目中必须维护：

```text
docs/license_notes.md
```

记录每个依赖的许可证和集成方式。

## 12. V0.1.11 状态记录

V0.1 MVP 已完成本地 PDBQT docking 最小闭环：工具检测、路径配置、项目创建、PDBQT 导入、box/Vina 参数配置、`vina_config.txt` 生成、run 准备、Vina 执行、log 解析、`scores.csv` 导出和 Markdown 报告导出。

V0.1.11 的重点是文档、使用教程、smoke test、FAQ 和路线图整理。下一阶段进入 V0.2 准备，重点评估 PDB/PubChem 下载、RDKit/Meeko 自动准备和更完善的错误引导。

仍然禁止在没有确认许可证和分发边界前直接复制第三方源码或二进制到 DockStart，尤其是 Open Babel、PLIP、MGLTools 等工具。
