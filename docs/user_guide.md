# DockStart User Guide

本文档面向第一次使用 AutoDock Vina 和 DockStart 的用户，说明如何从已有 PDBQT 文件完成 MVP 流程，并说明 V0.2 raw workflow 与 V0.3 preparation 入口。

## 选择使用模式

DockStart V0.8 开始把入口分成三种模式：

- **Basic Mode：已有 PDBQT**。你已经准备好 `receptor.pdbqt` 和 `ligand.pdbqt`，只需要配置 AutoDock Vina。这是最低依赖路径。
- **Assisted Mode：从 raw 文件准备 PDBQT**。你只有 PDB/CIF/SDF/MOL 等原始结构文件，需要配置 Python + RDKit + Meeko，DockStart 才能尝试自动准备 PDBQT。
- **Demo Mode：先看示例流程**。用于第一次体验软件流程。示例只用于流程演示，不用于科研结论。

如果 RDKit/Meeko 缺失，Assisted Mode 会不可用，但 Basic Mode 仍然可以继续，只要 Vina 和已经准备好的 PDBQT 可用。

V0.8.1 之后，侧边工作流会把“导入 PDBQT”作为独立步骤显示。你可以跳过 raw 下载和自动准备，直接从 Basic Mode 进入 Box、Vina 参数、配置和运行流程。

V0.8.2 之后，可以在创建项目页复制示例项目：

- Basic 示例：内含玩具 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`；
- Assisted 示例：内含玩具 `raw/receptor_demo.pdb` 和 `raw/ligand_demo.sdf`。

示例只用于熟悉软件流程，不用于真实 docking 结论。详细说明见 [demo_projects.md](demo_projects.md)。

V0.8.3 之后，首次启动向导会先问你想怎么开始：

- 我已有 PDBQT：进入最低依赖路径；
- 我只有 PDB/SDF：先检查 Python/RDKit/Meeko 是否可用；
- 我只是想先看示例：复制示例项目理解流程。

向导会列出当前缺什么和下一步建议，但不会自动安装 Vina、RDKit 或 Meeko。

V0.8.4 之后，如果工具链缺失，可以在工具链页查看“修复建议”。这些建议会区分 Vina、Python/RDKit/Meeko 和 Microsoft Store Python 等问题，并提供手动步骤；DockStart 不会自动安装工具或修改系统 PATH。详细说明见 [toolchain_repair_guide.md](toolchain_repair_guide.md)。

V0.8.5 之后，工具链页可以运行“安装后自检”，并导出本地 Markdown 诊断报告。报告用于排查环境问题，不会上传网络；其中可能包含本机工具路径，分享前请自行检查。

## 前置条件

你需要先准备好：

- AutoDock Vina，并确认 `vina` 或 `vina.exe` 可以被 DockStart 找到；
- 已经准备好的 `receptor.pdbqt`；
- 已经准备好的 `ligand.pdbqt`；
- 一个用于保存 DockStart 项目的本地目录。

V0.2.5 到 V0.2.10 可以从 RCSB PDB / PubChem 下载原始结构文件到 `raw/`，并显示 raw 文件状态、大小、修改时间和记录一致性。RCSB 支持 `pdb` / `cif`；PubChem 支持 CID 和名称查询。SMILES 查询当前只返回“暂未支持”的结构化提示。V0.3.0 新增自动准备状态入口，V0.3.1 新增 RDKit/Meeko 能力检测，V0.3.2 可以把 ligand SDF/MOL raw 文件尝试准备为 `prepared/ligand.pdbqt`，V0.3.3 可以把 receptor PDB/CIF raw 文件尝试准备为 `prepared/receptor.pdbqt`。运行 Vina 仍然需要 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`。手动准备说明见 [manual_pdbqt_preparation.md](manual_pdbqt_preparation.md)，验收说明见 [smoke_test.md](smoke_test.md)。

当前推荐流程：

```text
下载 raw 原始结构
检查 RDKit/Meeko 准备能力
准备或手动导入 prepared/receptor.pdbqt 和 prepared/ligand.pdbqt
设置 Box 和 Vina 参数
生成 vina_config.txt
运行 Vina
解析结果并导出 Markdown 报告
```

## 1. 配置工具路径

用户需要输入：

- AutoDock Vina 路径，可填写 `vina.exe` 的绝对路径，也可以留空让系统 PATH 检测；
- Python 路径，可使用系统默认 Python。

输出位置：

- 本地设置文件 `dockstart_settings.json`，该文件不会提交到 Git。

常见错误：

- 找不到 Vina：确认 Vina 已安装，或在设置页填写 `vina.exe` 的完整路径；
- 路径包含不存在的文件：重新选择实际存在的可执行文件；
- Python 检测失败：确认命令行中可以运行 `python --version`。

## 2. 创建项目

用户需要输入：

- 项目名称；
- 项目保存目录。

输出位置：

```text
project_name/
├─ raw/
├─ prepared/
├─ configs/
├─ runs/
├─ results/
├─ reports/
└─ project.json
```

常见错误：

- 项目目录已存在：换一个项目名，DockStart 不会覆盖已有项目；
- 项目名称包含非法字符：避免使用 Windows 文件名保留字符，如 `\ / : * ? " < > |`。

创建成功后，页面会提供两个入口：

- 下载原始结构文件：适合还没有 raw PDB/CIF/SDF 的情况；
- 直接导入 PDBQT：适合已经用外部工具准备好 receptor/ligand PDBQT 的情况。

## 3. 可选：下载原始结构文件

用户可以在 StructureFetchPage 下载：

- RCSB PDB 受体原始结构，例如输入 `1HSG`；
- PubChem CID 配体原始 SDF，例如输入 `2244`；
- PubChem 名称配体原始 SDF，例如输入 `aspirin`；
- SMILES 查询目前会显示暂未支持，不会调用 RDKit 或生成 3D。

输出位置：

```text
raw/receptor_1HSG.pdb
raw/receptor_1HSG.cif
raw/ligand_2244.sdf
raw/ligand_name_aspirin.sdf
```

`project.json` 会记录：

```json
{
  "receptor": {
    "source": "rcsb_pdb",
    "source_id": "1HSG",
    "query_type": "pdb_id",
    "downloaded_at": "2026-06-26T12:00:00+00:00",
    "raw_file": "raw/receptor_1HSG.pdb",
    "file": "prepared/receptor.pdbqt"
  },
  "ligand": {
    "source": "pubchem",
    "source_id": "2244",
    "query_type": "cid",
    "downloaded_at": "2026-06-26T12:00:00+00:00",
    "raw_file": "raw/ligand_2244.sdf",
    "file": "prepared/ligand.pdbqt"
  }
}
```

StructureFetchPage 会显示：

- receptor raw 文件状态；
- ligand raw 文件状态；
- raw 文件是否存在；
- 文件大小；
- 修改时间；
- 绝对路径；
- `record_consistent`，用于提示 project.json 记录和实际 raw 文件是否一致。

可以清除 raw 记录：

- 默认只清除 `source`、`source_id`、`query_type`、`downloaded_at` 和 `raw_file`；
- 不会清除 `receptor.file` 或 `ligand.file`；
- 不会删除 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`；
- 如果选择“同时删除 raw 文件”，DockStart 只允许删除项目 `raw/` 目录内的文件。

注意：

- raw 文件不能直接运行 AutoDock Vina；
- DockStart 可以在 V0.3 工具链条件满足时尝试自动准备 PDBQT；
- 下载 raw 文件后，下一步仍需进入 PreparationPage 准备 PDBQT，或手动准备并导入 `receptor.pdbqt` 和 `ligand.pdbqt`。

常见错误：

- PDB ID 不是 4 位字母/数字；
- PubChem CID 不是正整数；
- PubChem 名称为空或过长；
- SMILES 查询暂未支持；
- 网络超时或远端返回 404；
- raw 文件已存在且未开启 overwrite。
- project.json 记录了 raw_file 但文件被手动删除，此时 `record_consistent` 会显示需要检查。

## 4. 可选：检查自动准备状态

PreparationPage 会显示：

- 当前项目路径；
- receptor / ligand raw 文件；
- receptor / ligand prepared PDBQT 文件；
- Python、RDKit、Meeko 检测状态；
- receptor / ligand preparation status。

V0.3.0 只做准备状态模型、前置检查和重置。页面不会真正调用 RDKit/Meeko 生成 PDBQT。自动准备结果即使在后续版本生成，也仍需用户检查质子化、电荷、构象、受体链选择和结构完整性。

V0.3.1 会进一步显示 RDKit import / SDF 读取探测、Meeko import / ligand preparation / receptor preparation 能力状态。`unknown` 表示 DockStart 暂时无法确认该能力，不代表工具一定不可用。V0.3.1 阶段仍不会生成 PDBQT；V0.3.2 开始支持 ligand SDF/MOL 自动准备，V0.3.3 增加 receptor PDB/CIF 自动准备。

V0.3.2 新增“准备 ligand PDBQT”按钮：当 `ligand.raw_file` 是 SDF 或 MOL，且 Python、RDKit、Meeko 与 Meeko ligand preparation 能力可用时，可以生成 `prepared/ligand.pdbqt`。默认不会覆盖已有 ligand PDBQT；stdout、stderr 和日志会保存到 `prepared/logs/`。生成结果仍需要用户检查质子化、电荷、构象等问题。

V0.3.3 新增“准备 receptor PDBQT”按钮：当 `receptor.raw_file` 是 PDB 或 CIF，且 Python、Meeko 与 Meeko receptor CLI 可用时，可以生成 `prepared/receptor.pdbqt`。默认不会覆盖已有 receptor PDBQT；stdout、stderr 和日志会保存到 `prepared/logs/`。受体准备仍需要用户检查缺失残基、金属离子、水分子、辅因子、链选择和质子化状态。

V0.3.4 会在 PreparationPage 显示项目下一步建议。生成 config 或准备 run 时，如果 DockStart 发现已经下载了 raw receptor/ligand，但还没有 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`，会提示先准备 PDBQT；如果上一次 preparation 失败，会提示查看 preparation 日志。这个接入不会修改 Vina config、Vina 执行或结果解析逻辑。

V0.3.5 开始，每次自动准备都会生成独立记录目录：

```text
preparation/ligand_001/
preparation/receptor_001/
```

每个目录包含：

```text
metadata.json
stdout.txt
stderr.txt
command.json
input_snapshot.json
output_check.json
```

这些记录用于排查工具版本、输入、命令和输出状态。它们说明 preparation 过程可追踪，但不代表结果已经经过科学验证。

V0.3.6 的完整工作流可以理解为：

1. 下载 receptor raw 文件。
2. 下载 ligand raw 文件。
3. 检查 Python / RDKit / Meeko 能力。
4. 准备 ligand PDBQT。
5. 准备 receptor PDBQT。
6. 人工检查 prepared PDBQT。
7. 设置 Box。
8. 设置 Vina 参数。
9. 生成 `configs/vina_config.txt`。
10. 准备并运行 Vina。
11. 解析结果。
12. 导出 Markdown 报告。

自动准备不保证 protonation、电荷、构象、缺失残基、水、金属、辅因子或链选择一定正确，也不等于药效判断。

## 5. 导入 receptor.pdbqt

用户需要输入：

- 已经准备好的 receptor PDBQT 文件路径。

输出位置：

```text
prepared/receptor.pdbqt
```

`project.json` 会记录 receptor 的来源和项目内路径。

常见错误：

- 文件不存在：检查输入路径；
- 文件为空：重新准备 receptor PDBQT；
- 文件扩展名不是 `.pdbqt`：V0.1 只接受 PDBQT。

如果你已有 `receptor.raw_file`，且文件是 PDB 或 CIF，可以先在 PreparationPage 尝试“准备 receptor PDBQT”。如果 Meeko receptor CLI 不可用或准备失败，请参考 [manual_pdbqt_preparation.md](manual_pdbqt_preparation.md) 在外部工具中准备 receptor PDBQT。

## 6. 导入 ligand.pdbqt

用户需要输入：

- 已经准备好的 ligand PDBQT 文件路径。

输出位置：

```text
prepared/ligand.pdbqt
```

`project.json` 会记录 ligand 的来源和项目内路径。

如果你已有 `ligand.raw_file`，且文件是 SDF 或 MOL，可以先在 PreparationPage 尝试“准备 ligand PDBQT”。MOL2 和 SMILES 当前仍不支持自动准备。

常见错误：

- 文件不存在或为空；
- 误导入 PDB、SDF、MOL2：V0.1 不做自动格式转换，请先在外部工具中准备 PDBQT。

## 7. 设置 Box 参数

用户需要输入：

- `center_x`
- `center_y`
- `center_z`
- `size_x`
- `size_y`
- `size_z`

单位：

- `Å`

输出位置：

- `project.json` 的 `box` 字段。

常见错误：

- `size_x/y/z` 小于或等于 0：box 尺寸必须为正数；
- 输入非数字：请使用整数或小数；
- box 过大：运行会变慢，且可能降低搜索效率。

## 8. 设置 Vina 参数

用户需要输入：

- `exhaustiveness`：搜索彻底程度，越高越慢；
- `num_modes`：输出构象数量；
- `energy_range`：能量范围；
- `cpu`：CPU 数量，`0` 表示交给 Vina 自动决定；
- `seed`：随机种子，可留空。

输出位置：

- `project.json` 的 `vina` 字段。

常见错误：

- `exhaustiveness` 或 `num_modes` 不是正整数；
- `energy_range` 不是正数；
- `cpu` 为负数；
- seed 不是整数。

## 9. 生成 vina_config.txt

用户需要输入：

- 无额外输入；DockStart 会读取 `project.json` 中的 receptor、ligand、box 和 Vina 参数。

输出位置：

```text
configs/vina_config.txt
```

常见错误：

- receptor 或 ligand 尚未导入；
- 已下载 raw receptor/ligand，但尚未准备 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`；
- 上一次 receptor/ligand preparation 失败，需要回到 PreparationPage 查看日志；
- box 或 Vina 参数格式不合法；
- 项目目录不可写。

## 10. 准备 run

用户需要输入：

- 无额外输入；DockStart 会进行运行前检查。

输出位置：

```text
runs/run_001/metadata.json
runs/run_001/command_preview.txt
runs/run_001/config_snapshot.txt
```

常见错误：

- 找不到 Vina；
- 找不到 `configs/vina_config.txt`；
- receptor 或 ligand 文件缺失；
- run 目录已存在时，DockStart 会自动选择下一个 run id。

## 11. 执行 Vina

用户需要输入：

- 点击执行按钮。

输出位置：

```text
runs/run_001/stdout.txt
runs/run_001/stderr.txt
runs/run_001/log.txt
runs/run_001/out.pdbqt
runs/run_001/metadata.json
```

常见错误：

- Vina 返回非 0 exit code：查看 `stderr.txt` 和 `log.txt`；
- 没有生成非空 `out.pdbqt`：检查输入 PDBQT、box、参数和 Vina 版本；
- command 不是数组：重新准备 run。

## 12. 解析结果

用户需要输入：

- 已完成且状态为 `finished` 的 run。

DockStart 会从以下文件解析 Vina 结果表格：

```text
runs/run_001/log.txt
```

输出位置：

```text
runs/run_001/scores.csv
results/scores.csv
```

CSV 表头：

```csv
mode,affinity_kcal_mol,rmsd_lb,rmsd_ub
```

常见错误：

- run 状态不是 `finished`：先成功执行 Vina；
- `log.txt` 缺失或为空；
- `log.txt` 中没有 Vina score 表格；
- score 表格行格式异常。

## 13. 导出 Markdown 报告

用户需要输入：

- 已完成且已经解析出 `scores.csv` 的 run。

输出位置：

```text
runs/run_001/docking_report.md
reports/docking_report.md
```

报告包含：

- 项目信息；
- 输入文件；
- Box 参数；
- Vina 参数；
- 运行信息；
- Docking Score 结果；
- 重要说明和免责声明。

常见错误：

- `scores.csv` 不存在：先回到结果页解析结果；
- `metadata.json` 不存在：重新准备 run；
- `vina_config.txt` 不存在：重新生成配置文件；
- receptor 或 ligand 未记录：重新导入 PDBQT。

## 结果解释限制

Docking score 仅供结构结合趋势参考，不能替代实验验证。DockStart V0.1 不判断药效，不证明真实结合能力，不包含相互作用分析，也不包含分子动力学验证。
## V0.4.0 Viewer 数据模型说明

V0.4.0 已提供后端 viewer 读取接口，可以把项目内的 `raw/`、`prepared/` 和 `runs/{run_id}/out.pdbqt` 文本结构文件安全传给后续前端 viewer。V0.4.0 阶段还没有正式 3D 页面；V0.4.1 已接入最小 3Dmol.js ViewerPage。

这些接口只读取文本结构文件，不调用 RDKit、Meeko 或 AutoDock Vina，不做相互作用分析，也不会判断 docking pose 是否代表真实结合或药效。超过 20 MB 的结构文件会被拒绝一次性读取，并返回中文结构化提示。

V0.4.1 已新增最小 ViewerPage。进入项目后，可以从 PreparationPage 或 ImportPdbqtPage 点击“打开 3D 查看 / 查看 prepared 文件”，也可以在已有当前项目时从首页进入。页面支持选择 receptor raw、ligand raw、receptor prepared、ligand prepared 或最近 docking output，加载后可清空 viewer 或重新居中。Box 线框和 pose-score 对应表分别在 V0.4.2 与 V0.4.3 补齐。

V0.4.2 已在 ViewerPage 中加入 Box 可视化设置。页面会读取 `project.json.box`，显示 `center_x/y/z` 和 `size_x/y/z` 六个参数，单位为 Å；修改输入框时会刷新 viewer 中的 Box overlay，点击“保存 Box 参数”后写回同一个 `project.json.box` 字段。Box 可视化只是帮助查看搜索空间，不代表 DockStart 自动识别结合口袋。

V0.4.3 已支持 docking pose 查看。输入 `run_001` 这类 run_id 后，ViewerPage 可以读取 `runs/{run_id}/out.pdbqt`，列出 mode，并在 `scores.csv` 存在时显示 affinity、rmsd_lb、rmsd_ub。点击某个 mode 后，页面会尝试同时显示 prepared receptor 和选中的 pose。该功能只用于几何查看和结果复核，不做相互作用分析或药效判断。

V0.4.4 已把 viewer 状态接入项目 workflow status。项目状态会记录 raw/prepared/docking output 是否可查看，并给出推荐查看动作；BoxSetupPage 可以直接进入 ViewerPage 查看 Box，ResultPage 在 run finished 后可以进入 ViewerPage 查看 docking pose。

## V0.4 Viewer 使用流程

1. 创建项目，并通过 raw 下载或手动导入准备结构文件。
2. 在 PreparationPage 或 ImportPdbqtPage 点击“打开 3D 查看 / 查看 prepared 文件”进入 ViewerPage。
3. 在结构来源中选择 receptor raw、ligand raw、receptor prepared、ligand prepared 或 docking output。
4. 点击“加载结构”，确认文件路径、格式和大小。
5. 在 Box 可视化设置中调整 center 和 size，确认 overlay 位置后点击“保存 Box 参数”。
6. 运行 Vina 并解析结果后，在 ViewerPage 输入 run_id，读取 pose 列表并选择 mode 查看。

ViewerPage 只做几何查看和流程复核，不做 pocket prediction、PLIP/ProLIF、相互作用解释、分子动力学或药效判断。自动准备和 3D 显示都不能替代用户对质子化、电荷、缺失残基、水、金属、辅因子和 box 合理性的科学检查。

## V0.5.1 项目总览 Dashboard

V0.5.1 开始，用户创建项目后会回到 ProjectDashboardPage，而不是直接散落到某个功能页。Dashboard 会读取现有 `get_project_workflow_status`，展示：

- 项目名称、项目目录、创建时间和更新时间；
- raw receptor / raw ligand 状态；
- prepared receptor / prepared ligand 状态；
- Box 参数、Vina 参数和 config 状态；
- latest run 状态；
- report 是否已经具备导出条件；
- 下一步推荐动作。

Dashboard 中的快捷操作卡片会跳转到结构获取、PDBQT 准备、Box、3D Viewer、config、run、result/report 等现有页面。它只整理入口和状态展示，不新增科学功能，也不会改变 Vina、RDKit、Meeko 或 viewer 的后端行为。

## V0.5.2 工作流 Stepper

V0.5.2 在 Dashboard 中加入完整工作流 stepper，并在 Sidebar 中显示简化步骤状态。Stepper 覆盖：

1. 创建项目
2. 获取 raw 结构
3. 准备 PDBQT
4. 设置 Box
5. 设置 Vina 参数
6. 生成 config
7. 准备 run
8. 执行 Vina
9. 解析结果
10. 导出报告
11. 3D 查看 / pose 查看

每一步会显示 `未开始`、`可进行`、`需确认`、`已完成`、`未就绪` 或 `失败`。这些状态来自现有 project/workflow 信息的前端推导，用来引导用户下一步操作；它不会自动判断分子是否科学合理，也不会新增 docking、preparation 或相互作用分析能力。

## V0.5.4 raw 与 PDBQT 准备页面

V0.5.4 调整了两个核心页面的信息层级：

- StructureFetchPage 标题调整为“获取原始结构文件”，按 receptor / ligand 两栏展示 raw 文件状态、下载表单、overwrite 说明、清除 raw 记录和下一步入口。
- PreparationPage 标题调整为“准备 Vina 输入文件 PDBQT”，按工具链状态、receptor preparation、ligand preparation 展示 raw input、prepared output、Python/RDKit/Meeko 状态、prepare 按钮和日志路径。

这次改动只改善页面结构和中文提示，不改变 RCSB/PubChem 下载逻辑，不改变 RDKit/Meeko preparation 核心逻辑，也不新增 Open Babel、PLIP、MGLTools 或相互作用分析。

## V0.5.5 Viewer 工作区

V0.5.5 将 ViewerPage 整理为三栏工作区：

- 左侧：结构来源、docking pose 读取和 Box 参数控制。
- 中间：3Dmol.js 画布、重新居中和清空 viewer。
- 右侧：当前文件状态、可查看文件列表、pose 列表和技术错误详情。

这次改动只调整前端信息架构。ViewerPage 仍然只做 raw/prepared/docking output 的几何查看、Box overlay 和 pose mode 切换，不做 pocket prediction、PLIP/ProLIF、相互作用解释、药效判断或 Vina 算法修改。

## V0.5.6 Vina 运行流程页面

V0.5.6 在 Vina 主线页面顶部加入统一流程条：

```text
生成 config -> 准备 run -> 执行 Vina -> 解析结果 -> 导出报告
```

该流程条出现在 VinaConfigPage、RunPreparePage、RunExecutePage、ResultPage 和 ReportPage，用于提示当前步骤和 run_id。VinaConfigPage 与 RunPreparePage 也改用统一的 warning / command-result 展示方式。

这次改动不改变 `vina_config.txt` 生成内容，不改变 AutoDock Vina 调用命令，不改变 score 解析逻辑，也不改变 Markdown 报告字段。

## V0.5.7 内置帮助与新手引导

V0.5.7 新增 HelpPage，并在 Sidebar 中开放“文档帮助”入口。帮助页说明：

- 推荐新手流程；
- raw 文件与 prepared PDBQT 的区别；
- `configs/vina_config.txt` 和 `runs/run_XXX/` 的作用；
- 工具链、结构获取、PDBQT 准备、3D 查看、Vina 运行和报告导出的页面定位；
- DockStart 不做药效判断、相互作用解释或 pocket prediction。

项目总览在没有项目时也会显示一组 onboarding 步骤，帮助用户先创建项目并理解后续流程。这些引导只改变前端说明，不会自动安装工具、自动运行 Vina 或自动判断科学结论。

## V0.5.8 前端冻结审计

V0.5.8 只做版本、文档和前端工作流一致性审计。当前 V0.5 的真实含义是“前端工作流整改”，不是新增相互作用分析或科学判断。

审计确认：

- AppShell / Sidebar / Dashboard / HelpPage 已接入；
- raw、preparation、viewer、Vina run、result 和 report 页面均保留最小可用入口；
- Vina config 生成、Vina 执行、score 解析和 Markdown 报告导出语义未改变；
- 未新增 PLIP/ProLIF、相互作用分析、pocket prediction、药效判断、Open Babel、MGLTools 或外部 CDN。

## V0.5.9 前端可用性验收

V0.5.9 进行了一轮真实前端可用性验收和小修：

- Vite 前端构建通过，并确认 Tauri dev 能启动出 DockStart 桌面进程；
- 无项目 Dashboard、HelpPage、项目必需页面重定向和工具链页 fallback 状态可打开；
- 修复浏览器校验时的本地 favicon 404；
- 无项目时 Sidebar 不再显示一串不可执行的 workflow 状态；
- 没有选中 run_id 时，执行页、结果页和报告页会显示清楚的“需要先准备/执行 run”占位说明；
- ToolchainStatusPage 的 RDKit/Meeko 文案校准为“本页只检测，PreparationPage 才触发准备”。

本轮仍不新增科学功能，不改变 Vina、RDKit/Meeko preparation、score 解析或报告导出逻辑。

## V0.6.3 首次启动工具链引导

首次打开 DockStart 且尚未创建项目时，项目总览会先提示工具链状态：

- AutoDock Vina 是否可用；
- 当前 Python 来源是 bundled、configured 还是 current_environment；
- RDKit 是否可导入；
- Meeko 是否可导入；
- 下一步建议是配置 Vina、配置 Python 工具链，还是创建项目。

工具链页提供“复制当前 Python 路径”按钮，并解释 bundled、configured、PATH/current_environment 的含义。DockStart 仍不会自动安装 RDKit/Meeko，也不会自动修改系统 PATH。
