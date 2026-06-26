# PROJECT.md

# DockStart
 项目说明

## 1. 项目定位

DockStart 是一个基于 AutoDock Vina 的第三方开源中文分子对接工作台，目标是帮助初学者完成受体/配体准备、对接箱体设置、AutoDock Vina 参数生成、任务运行、结果解析和报告导出。

产品定位正在从“外部工具调用器”调整为“开箱即用的一站式分子对接平台”。DockStart Full 的最终目标是分发简单、内置工具链、开箱即用、中文引导，并逐步覆盖分子对接全过程。当前 V0.1 是 Lite MVP，依赖用户已有 PDBQT 和 Vina，只是阶段性实现，不是最终形态。V0.2.3 已完成 bundled Python runtime 的路径解析、manifest 完整性检查和 ToolchainStatusPage 展示。V0.2.5 开始 Structure acquisition line，只下载 RCSB PDB / PubChem CID 原始结构并记录来源；V0.2.6 增强 raw 文件状态展示和 raw 记录管理；V0.2.7 增强 RCSB/PubChem raw 来源查询；V0.2.8 增强 raw/prepared 流程 UI 引导；V0.2.9 新增手动 PDBQT 准备指南。当前仍未实现 PDBQT 自动生成或 RDKit/Meeko 分子处理。

本项目不是新的分子对接算法，也不修改 AutoDock Vina 的打分函数或搜索算法。项目重点是：

* 降低 AutoDock Vina 使用门槛；
* 提供中文流程引导；
* 减少文件格式、路径、参数设置错误；
* 记录每一步输入、输出、参数和日志；
* 提高课程设计、本科毕设、教学演示和初级科研预实验的可复现性。

## 2. 核心原则

### 2.1 不改核心算法

第一阶段不得修改 AutoDock Vina 核心算法，不得修改 scoring function，不得声称本项目提高了对接准确率。

V0.1 Lite 只作为图形化前端、流程管理器、参数生成器、运行器和结果解析器。DockStart Full 可以内置和管理工具链，但仍然不修改 AutoDock Vina 算法，也不宣称提高 docking 准确率。

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

AutoDock Vina、Meeko、RDKit 可作为核心优先支持对象，但当前 Meeko/RDKit 仍只做 Python import 检测，不做分子准备或处理。DockStart Full 工具链优先级为：

```text
内置工具 > 用户配置路径 > 系统 PATH
```

Open Babel、PLIP、MGLTools 等工具许可证或依赖更复杂，暂不进入核心内置包，只能作为外部可选集成继续评估。

Python runtime 当前解析优先级为：

```text
bundled > configured > current_environment
```

其中 bundled Python 只表示 `resources/python/python.exe` 存在且可检测。当前仓库只提交 `resources/python/README.md`，真实 runtime 文件被 `.gitignore` 忽略。

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

## 13. DockStart Full 工具链方向

V0.1 之后的产品叙事应明确区分：

* V0.1 Lite MVP：依赖用户已有 PDBQT 和 Vina，主要验证本地 docking 闭环。
* DockStart Full：面向普通用户的开箱即用平台，逐步内置工具链并覆盖分子对接全过程。

建议工具链资源结构：

```text
resources/
├─ tools/
│  └─ vina/
├─ python/
└─ licenses/
```

许可证策略：

* AutoDock Vina 可作为候选内置工具，但必须保留许可证、版本和来源说明。
* RDKit 可作为候选内置组件，但必须保留许可证和依赖说明。
* Meeko 可作为候选内置组件，但必须补充 LGPL 合规说明。
* Open Babel / MGLTools / PLIP 暂不进入核心内置包。

后续架构设计详见 `docs/toolchain_design.md` 和 `docs/toolchain_runtime.md`。在没有确认许可证和分发边界前，不得把第三方源码或二进制直接复制进发布包。

### 13.1 V0.2.3 / V0.2.4 当前校准

V0.2.3 的真实含义是“内置 Python runtime 解析与完整性检查”，不是“RDKit/Meeko 已内置”或“自动准备分子已实现”。

当前已经具备：

* `resources/python/python.exe` 的路径解析；
* `resources/toolchain_manifest.json` 中 `bundled_python` 的版本、来源、`sha256` 记录；
* ToolchainStatusPage 展示 bundled Python 是否存在、解析路径、版本、`sha256` 和 Python 来源；
* `scripts/prepare_bundled_python.py` 从本地 Python runtime 复制文件、计算 `python.exe` sha256、读取版本并更新 manifest。

当前明确没有实现：

* PDB/PubChem 下载；
* PDB/SDF/MOL2 自动转 PDBQT；
* RDKit 配体处理；
* Meeko 受体/配体准备；
* Open Babel；
* PLIP/MGLTools；
* 3D 可视化；
* 药效判断。

`scripts/prepare_bundled_python.py` 不联网、不安装 Python 包、不安装 RDKit、不安装 Meeko。后续如真正内置 RDKit/Meeko，必须单独审查许可证、体积、更新机制和分发策略。

### 13.2 V0.2.5 原始结构下载基础层

V0.2.5 的真实含义是“下载和记录 raw 原始结构来源”，不是“自动准备 docking 输入”。

当前已经具备：

* 通过 RCSB PDB ID 下载受体原始结构到 `raw/receptor_{PDB_ID}.pdb` 或 `.cif`；
* 通过 PubChem CID 下载配体原始 SDF 到 `raw/ligand_{cid}.sdf`；
* 在 `project.json` 的 receptor/ligand 中记录 `source`、`source_id` 和 `raw_file`；
* 保留 `receptor.file` 和 `ligand.file` 作为 prepared PDBQT 路径，不用 raw 文件覆盖。

当前明确没有实现：

* raw PDB/SDF/MOL2 自动转 PDBQT；
* RDKit 配体处理；
* Meeko 受体/配体准备；
* Open Babel、PLIP、MGLTools；
* 3D 可视化；
* Vina 运行流程变更；
* 药效判断。

### 13.3 V0.2.6 raw 文件管理增强

V0.2.6 的真实含义是“管理 raw 文件记录和状态”，不是“把 raw 文件转成 prepared PDBQT”。

当前已经具备：

* `get_raw_files_status(project_dir)` 返回 receptor/ligand raw 状态，包括 `source`、`source_id`、`raw_file`、`exists`、`size_bytes`、`modified_at`、`absolute_path` 和 `record_consistent`；
* `clear_receptor_raw_record(project_dir, delete_file=False)`；
* `clear_ligand_raw_record(project_dir, delete_file=False)`；
* 清除 raw 记录会清空 `source`、`source_id`、`raw_file`、`downloaded_at` 和 `query_type`；
* 清除 raw 记录不会清空 `receptor.file` / `ligand.file`，也不会删除 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`；
* `delete_file=True` 时只允许删除项目 `raw/` 目录下的文件；
* StructureFetchPage 显示 raw 状态卡片、overwrite 警告和清除 raw 记录按钮。

当前仍然明确没有实现：

* raw PDB/SDF/MOL2 自动转 PDBQT；
* RDKit 配体处理；
* Meeko 受体/配体准备；
* Open Babel、PLIP、MGLTools；
* 3D 可视化；
* Vina 运行流程变更；
* 药效判断。

### 13.4 V0.2.7 结构来源查询增强

V0.2.7 的真实含义是“扩展 raw 来源查询方式”，不是“分子处理或 PDBQT 自动准备”。

当前已经具备：

* RCSB PDB 下载支持 `pdb` 和 `cif`；
* PubChem CID 查询保持兼容；
* PubChem 名称查询保存为 `raw/ligand_name_{name}.sdf`；
* SMILES 查询返回中文结构化“暂未支持”提示；
* `project.json` 继续记录 `source`、`source_id`、`query_type`、`raw_file` 和 `downloaded_at`。

当前仍然明确没有实现：

* SMILES 解析；
* raw PDB/SDF/MOL2 自动转 PDBQT；
* RDKit 配体处理；
* Meeko 受体/配体准备；
* Open Babel、PLIP、MGLTools；
* 3D 可视化；
* Vina 运行流程变更；
* 药效判断。

### 13.5 V0.2.8 raw/prepared 流程 UI 引导增强

V0.2.8 的真实含义是“减少用户把 raw 文件误认为可直接运行 Vina 的风险”，不是“实现自动准备”。

当前已经具备：

* 首页显示“下载 raw 原始结构 → 手动准备 PDBQT → 导入 prepared PDBQT → 设置参数 → 运行 Vina”的推荐流程；
* ProjectCreatePage 提供 raw 下载和直接导入 PDBQT 两个入口；
* ImportPdbqtPage 解释 raw 文件和 prepared PDBQT 的区别；
* StructureFetchPage 下载后提示下一步仍需手动准备并导入 PDBQT；
* ToolchainStatusPage 明确 Meeko/RDKit 当前只是 import 检测，不会自动处理分子。

当前仍然明确没有实现：

* raw PDB/SDF/MOL2 自动转 PDBQT；
* RDKit 配体处理；
* Meeko 受体/配体准备；
* Open Babel、PLIP、MGLTools；
* 3D 可视化；
* Vina 运行流程变更；
* 药效判断。

### 13.6 V0.2.9 手动 PDBQT 准备指南

V0.2.9 的真实含义是“补充人工准备说明和许可证边界”，不是“实现自动准备”。

当前已经具备：

* `docs/manual_pdbqt_preparation.md`；
* raw 文件和 prepared PDBQT 的概念说明；
* Vina 需要 PDBQT 的原因说明；
* 下载 PDB/CIF/SDF 后不能直接运行 Vina 的说明；
* Meeko、AutoDockTools/MGLTools、Open Babel 作为可选外部工具的说明；
* Open Babel、MGLTools、PLIP 当前不内置的许可证边界；
* DockStart 当前不保证外部工具生成的 PDBQT 科学正确性的说明。

当前仍然明确没有实现：

* raw PDB/SDF/MOL2 自动转 PDBQT；
* RDKit 配体处理；
* Meeko 受体/配体准备；
* Open Babel、PLIP、MGLTools 接入；
* 3D 可视化；
* Vina 运行流程变更；
* 药效判断。
