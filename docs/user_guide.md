# DockStart User Guide

本文档面向第一次使用 AutoDock Vina 和 DockStart 的用户。正式发布基线仍以 Release 页面为准；当前源码版本为 v0.12.2，并继续兼容 Basic/Assisted 的最小对接流程。

## 按现有文件选择入口

当前创建项目页按用户手上的输入文件提供三种入口：

- **已有 PDBQT（直接使用）**：你已经准备好 `receptor.pdbqt` 和 `ligand.pdbqt`，只需要配置 AutoDock Vina。这是最低依赖路径，对应 Basic profile 的核心能力。
- **PDB/CIF + SDF/MOL/MOL2（准备并转换）**：你只有受体 PDB/CIF 与配体 SDF/MOL/单分子 MOL2；Assisted profile 已随附固定 Python + RDKit + Meeko，可离线尝试准备并转换为 PDBQT。兼容的用户配置 Python 仍优先。
- **示例项目（快速体验）**：复制内置小型项目，先熟悉完整操作路径。示例只用于流程演示，不用于科研结论。

如果随附或用户配置的 RDKit/Meeko 检测失败，Assisted Mode 会不可用，但 Basic Mode 仍然可以继续，只要 Vina 和已经准备好的 PDBQT 可用。

### 源码工作树：选择本次运行任务

尚未发布的源码工作树把“输入文件从哪里来”和“要执行什么计算”分开选择。创建项目时可选择：

- **搜索候选姿势**：在 Box 内执行全局对接，输出多个候选构象和 `scores.csv`；
- **评分当前姿势**：只计算当前输入姿势的评分与能量分项，不搜索新姿势，也不生成新的 PDBQT；
- **局部优化并比较**：先记录输入姿势评分，再在其附近执行局部优化，输出 `optimized.pdbqt` 并比较优化前后变化。

“单配体任务”“串行批量筛选”和“多配体共同对接（实验性）”是运行工作台中的三种不同工作区模式。导入多个配体仍默认用于串行批量筛选，不会自动切换为共同对接；共同对接必须由用户显式选择。

评分或局部优化要求配体已经位于受体中的待评价位置，并与受体使用同一坐标系。进入 Vina 运行工作台后：

1. 在同一 3D 视图中查看受体和配体；
2. 人工确认当前坐标关系符合研究输入；
3. 点击“确认当前输入姿势”；
4. 再保存参数并执行运行。

评价任务不提供“在线 RCSB 受体 + 独立 PubChem 配体”的组合入口，因为独立 PubChem 构象不在所选受体坐标系中。请导入已经共同定位的 PDBQT，或导入来自同一坐标系的本地原始结构，并在转换后复核坐标。

确认记录与本次运行实际使用的刚性受体、可选柔性侧链和配体 PDBQT 的 SHA256 绑定。重新导入、覆盖、重建柔性受体或手动替换任一输入后，原确认会自动失效，需要重新查看并确认。准备 run 后，DockStart 还会在真正启动 Vina 前要求冻结确认与该 run 的不可变输入快照一致。DockStart 只记录用户完成了这项复核，不会把 3D 显示解释为软件已经验证结合姿势。

V0.8.1 之后，侧边工作流会把“导入 PDBQT”作为独立步骤显示。你可以跳过 raw 下载和自动准备，直接从 Basic Mode 进入 Box、Vina 参数、配置和运行流程。

V0.8.2 之后，可以在创建项目页复制示例项目：

- Basic 示例：内含玩具 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`；
- Assisted 示例：内含玩具 `raw/receptor_demo.pdb` 和 `raw/ligand_demo.sdf`。

示例只用于熟悉软件流程，不用于真实 docking 结论。详细说明见 [demo_projects.md](demo_projects.md)。

V0.8.3 之后，首次启动向导会先问你想怎么开始：

- 我已有 PDBQT：进入最低依赖路径；
- 我只有 PDB/SDF：先检查 Python/RDKit/Meeko 是否可用；
- 我只是想先看示例：复制示例项目理解流程。

向导会列出当前缺什么和下一步建议。Assisted 的固定工具链随安装包提供；应用运行时不会联网安装包或修改系统 Python。

V0.8.4 之后，如果工具链缺失，可以在工具链页查看“修复建议”。这些建议会区分 Vina、Python/RDKit/Meeko 和 Microsoft Store Python 等问题，并提供手动步骤；DockStart 不会自动安装工具或修改系统 PATH。详细说明见 [toolchain_repair_guide.md](toolchain_repair_guide.md)。

V0.8.5 之后，工具链页可以运行“安装后自检”，并导出本地 Markdown 诊断报告。报告用于排查环境问题，不会上传网络；其中可能包含本机工具路径，分享前请自行检查。

## 前置条件

所有模式都需要一个用于保存 DockStart 项目的本地目录。Basic 与 Assisted 都随附 AutoDock Vina；如果工具检测页仍提示不可用，请先执行显式重检。

- 选择“已有 PDBQT（直接使用）”时，需要 `receptor.pdbqt` 和 `ligand.pdbqt`；
- 选择“PDB/CIF + SDF/MOL（准备并转换）”时，需要本地原始结构，或在项目创建后使用在线搜索下载；
- 选择“示例项目（快速体验）”时不需要自行准备输入文件。

V0.2.5 到 V0.2.10 可以从 RCSB PDB / PubChem 下载原始结构文件到 `raw/`，并显示 raw 文件状态、大小、修改时间和记录一致性。RCSB 支持 `pdb` / `cif`；PubChem 支持 CID 和名称查询。SMILES 查询当前只返回“暂未支持”的结构化提示。V0.3.0 新增自动准备状态入口，V0.3.1 新增 RDKit/Meeko 能力检测，V0.3.2 可以把 ligand SDF/MOL raw 文件尝试准备为 `prepared/ligand.pdbqt`，V0.3.3 可以把 receptor PDB/CIF raw 文件尝试准备为 `prepared/receptor.pdbqt`。运行 Vina 仍然需要 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`。手动准备说明见 [manual_pdbqt_preparation.md](manual_pdbqt_preparation.md)，验收说明见 [smoke_test.md](smoke_test.md)。

已有 PDBQT 的推荐流程：

```text
导入 prepared/receptor.pdbqt 和 prepared/ligand.pdbqt
设置 Box 和 Vina 参数
生成 vina_config.txt
运行 Vina
解析结果并导出 Markdown 报告
```

原始结构的推荐流程：

```text
导入本地 PDB/CIF + SDF/MOL，或在线搜索下载
准备并转换 receptor / ligand PDBQT
人工检查 prepared/receptor.pdbqt 和 prepared/ligand.pdbqt
设置 Box 和 Vina 参数
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

创建项目时先选择输入来源：

- 已有 PDBQT：选择本地 receptor/ligand PDBQT，创建后进入最短运行流程；
- PDB/CIF + SDF/MOL：可以选择本地原始结构，也可以先创建项目，再使用常驻的“在线搜索并下载”入口；
- 示例项目：选择示例类型和保存目录，复制后直接进入对应流程。

## 3. 可选：在线搜索并下载原始结构

此入口只在主动搜索/下载时联网；返回结构准备页后仍可随时重新进入。用户可以下载：

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

## 4. 准备并转换为 PDBQT

原始结构不能直接交给 AutoDock Vina。结构准备页提供明确的“转换受体为 PDBQT”和“转换配体为 PDBQT”动作，并显示后台任务进度、失败原因与恢复建议。页面会显示：

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

### 4.1 在 SSH 或 CI 中只读审查结构

源码环境提供不启动桌面界面、也不探测 RDKit/Meeko 的结构审查命令。默认输出保持原有 JSON 契约：

```powershell
$env:PYTHONPATH = (Resolve-Path .\backend)
python -B -m dockstart_core.preparation structure-review "<project_dir>"
```

需要便于终端阅读的中文摘要时，显式选择文本格式：

```powershell
python -B -m dockstart_core.preparation structure-review "<project_dir>" --format text
```

文本摘要包括：

- 受体和配体的项目相对路径、格式与记录来源；
- 已有结构审查中可以直接从文件观察到的保守事实；
- 全部 `warning` 和 `unknown` 项及其项目内证据路径；
- 科学免责声明。

该命令只读取 `project.json` 和项目内结构文件。即使打开的是缺少当前 schema 字段的旧项目，也只在内存中迁移，不写回 `project.json`、不创建迁移备份或项目锁文件。目录推断会先确认解析后的目录和文件仍位于项目根目录内；指向项目外的 symlink/junction 会被拒绝并显示为非阻断的路径安全 warning，目录枚举失败也会转为结构化检查项，不会把 traceback 写到 stderr。

文本格式只显示项目文件夹名和项目相对路径，不显示完整绝对路径。文件来源、检查名称、检查消息、证据、错误说明、修复建议或免责声明等任意动态字段只要包含 Windows、UNC 或 POSIX 绝对路径，整个字段都会替换为固定的隐藏提示。默认 JSON 仍保留既有机器可读契约。

`--format` 只接受 `json` 或 `text`。文本审查成功时退出码为 `0`，项目读取等业务失败时为 `1`；非法或重复的 `--format` 返回错误码 `STRUCTURE_REVIEW_FORMAT_INVALID` 的中文结构化 JSON，并使用退出码 `2`。为兼容既有调用，省略 project_dir 的旧 JSON 参数错误仍保持退出码 `0`。

这份摘要不是结构修复、质子化判断、链选择、辅因子处理或科学验证，也不会运行 AutoDock Vina。Docking score 仅供结构结合趋势参考，不能替代实验验证。

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

如果你已有 `ligand.raw_file`，且文件是 SDF、MOL 或单分子 MOL2，可以先在 PreparationPage 尝试“准备 ligand PDBQT”。SMILES 以及批量/多记录 MOL2 当前仍不支持自动准备。

常见错误：

- 文件不存在或为空；
- 把不受支持的配体 PDB、SMILES 或批量/多记录 MOL2 当作 prepared PDBQT 导入；请改用受支持的 raw 格式，或先在外部工具中准备 PDBQT。

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

工具链页提供“复制当前 Python 路径”按钮，并解释 bundled、configured、PATH/current_environment 的含义。v0.12.0 Assisted 已随附 RDKit/Meeko fallback；DockStart 运行时不会联网安装包，也不会自动修改系统 PATH。

## V0.12.x 串行批量筛选与结果工作区

批量筛选用于让多个配体依次对接同一受体。它不是让多个配体同时进入同一次搜索。

1. 在配体库入口选择多个 PDBQT、SDF、MOL，或选择包含这些文件的文件夹。文件夹会递归扫描，链接和其他格式不会进入导入结果。
2. 查看导入预览中的“记录、可用、重复、失败、已选”。多分子 SDF 会按原始 1-based 记录编号逐条准备；一条记录失败不会隐藏或阻断其他有效记录。重复内容和失败记录不能勾选，可用记录可全选、清空或逐条选择。
3. 在同一工作台查看将被冻结的受体、Box、基础 Vina 参数，以及适用于全局对接的 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine`、`force_even_voxels`，再设置单任务 CPU、失败重试次数与 Top N。
4. 创建并开始队列。任务按稳定顺序串行运行；单项失败不会阻止后续配体。
5. 运行中可请求安全取消。当前配体完成写盘后队列停止，之后可显式恢复未完成项。
6. 在“配体结果工作区”查看全部条目，可按名称或错误搜索，按状态筛选，并按评分、导入顺序、名称或状态排序。
7. 成功项可点击“查看构象”。DockStart 使用本次筛选冻结的受体和该配体成功 attempt 的输出，不读取后来替换的项目当前结构；新记录会在显示前核对 SHA256。
8. 队列完成、含失败项完成或安全取消后，可生成 `screening/results/screening_report.md`。完整 CSV 位于 `screening/results/screening_summary.csv`，Top N 位于 `screening/results/screening_top_n.csv`。
9. 归档后切换到“历史归档”，可按时间查看归档列表，并只读浏览该批次的完整结果、Top N 和报告状态。
10. 历史成功项仍可点击“查看构象”。DockStart 按 `archive_id` 定位选定归档，只读取该归档中对应配体的 Mode 1 和冻结受体，不会误读当前活动队列中的同名配体。
11. 如需比较两个历史批次，请在历史列表中恰好选择两个不同的有效归档。先选择的归档是基线，后选择的归档是对照。
12. 比较结果只按冻结 ligand 输入文件的 SHA256 匹配配体，不按文件名、显示名称或导入顺序猜测身份。评分和排名差值均按“对照－基线”计算。
13. 如需移动或留存一条历史记录，点击该归档的“导出 ZIP”，选择保存位置。GUI 不会替换已有文件；如果保存位置已经被占用，原文件保持不变，请重新导出并选择新文件名。完成后界面会显示 ZIP 路径、大小、条目数、源完整性结论和 ZIP SHA256，可直接复制路径或哈希。

创建队列后，上述六项高级参数会固定在本次筛选状态中，并写入每个配体的独立 config；后来修改项目参数不会改变已经创建的队列。队列区会持续显示本次冻结的 Box、基础参数和六项高级参数，终态 `screening_report.md` 也会记录这些值。`no_refine` 与 `force_even_voxels` 只有在当前 Vina 运行时明确声明支持且满足安全版本要求时才可启用；开始或恢复队列时，DockStart 会统一复核所需能力。每个 attempt 会把冻结受体和当前配体写入独立目录，并在 Vina 启动前、返回后分别核对受体、配体、配置和当前 Vina 二进制的文件大小与 SHA256；全部一致才会标记成功，证据保存在 `attempt.json`。归档详情与双归档比较还会复核保存的 attempt 输入和配置；Vina 二进制本身不会复制进归档。旧归档缺少全部或部分逐次运行证据时仍可读取，归档详情会提示该记录不能视为完整验证。`unbound_energy` 只适用于刚性单配体 `score_only`，不会进入批量队列或逐配体 config。

新建队列会把九项资源限制及其 SHA256 一并保存。开始或恢复任务时会再次检查重试次数、Top N、配体数量、单配体大小、总输入大小、CPU、搜索彻底程度、构象数与 Box 上限；归档详情和比较使用相同规则。手工放宽限制、写入错误类型或超过应用硬上限时会阻止运行或读取。旧记录没有这些字段时按当前默认上限解释，并在详情中保留兼容标记。

配体库导入在启动 RDKit/Meeko 前先检查源文件大小、展开后的逻辑记录数和单记录大小。准备后的 PDBQT 按文件内容 SHA256 去重，而不是只比较文件名；两个名称不同但产生完全相同 PDBQT 的记录只保留一个可建队项，两个来源仍写入审计索引。创建队列时会再次核对 staging 文件大小与 SHA256，并把源文件、record index、record SHA256 和显示名称写入任务及逐次运行记录。当前只冻结这些来源身份，不复制原始 SDF record 拓扑，因此仍不能据此生成批量 SDF。

结果窗口默认只在用户点击配体时加载该项结构，避免把整个配体库一次性送入 3D 查看器。旧归档若没有输出 SHA256，仍可查看，但界面会明确标为“历史记录未完全验证”；这表示无法完成现代记录具备的输出完整性核对。已有哈希与文件不一致时会拒绝显示。

归档列表不会隐藏损坏记录。若归档标识、清单、状态文件或状态哈希校验失败，该条目仍会显示错误原因，但详情、报告入口和 3D 构象会被阻止。请保留原目录用于排查，不要复制活动状态文件覆盖归档。

开始比较前，DockStart 会实际读取并核对两个归档中冻结受体和全部冻结配体输入的文件大小与 SHA256。协议指纹覆盖受体 SHA256、评分函数、Box 的三个中心坐标和三个尺寸、Vina 版本与二进制 SHA256，当前批量基础参数（搜索彻底程度、输出构象数、能量范围、单任务 CPU 和随机种子），以及 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine`、`force_even_voxels`。只有协议指纹相同、该 SHA256 在两侧均唯一、两侧条目均成功且评分与排名有效时，界面才显示差值。协议不一致时仍可并排查看两批记录，但不会计算评分或排名差值；同一归档内出现重复 ligand 输入 SHA256 时，对应身份会标记为 `ambiguous`，不会由名称代替匹配。

导出的 ZIP 根目录包含 `dockstart_screening_export.json`，归档内容位于 `payload/<archive_id>/`。根清单记录允许导出的精确成员、逐文件大小与 SHA256、payload 树哈希、源归档完整性状态和兼容警告；DockStart 写完后会重新核对 ZIP 的 CRC、成员集合和逐文件哈希。现代归档还会核对 `attempt.json` 与冻结 attempt 记录的一致性，并验证完整汇总、Top N、Markdown 实验记录和构象输出的历史大小与 SHA256。旧归档只要仍能安全读取即可导出，但缺少上述历史凭据的产物会标为“部分验证”；导出时为 ZIP 新计算的哈希不会被当作历史真实性证据。

归档 ZIP 的边界：

- 它是只读实验记录，不是可直接导入的 DockStart 项目，也不能恢复活动队列或继续运行；
- 不包含 Vina 可执行文件、项目当前状态、活动队列、staging 或其他未列入清单的文件；
- 清单 SHA256 用于核对导出包的字节是否改变，不是数字签名，也不能单独证明历史记录的科学真实性；
- 归档内已有的 JSON 可能保留本机绝对路径。导出不会自动匿名化；对外分享前请先评估路径和文件名是否包含敏感信息。

当前边界：

- 只支持刚性受体和 Vina/Vinardo 的全局对接；继承上述六项适用高级参数，但不继承单配体 `score_only`、`local_only` 或 `unbound_energy`；
- 队列为串行执行，不声称支持并行筛选；
- 配体库预览暂不显示形式电荷、重原子数和可旋转键，也不支持对失败准备项单独重试或导出失败清单；
- 只有 PDBQT 时不能可靠恢复键级，因此不会从 PDBQT 猜测并生成 SDF 汇总；
- 历史归档是不可编辑的运行证据；浏览、双归档比较与 ZIP 导出均为只读，不会改写 `project.json` 或任何归档，也不能恢复活动队列、取消、恢复、重试或修改参数；
- Top N 只是在相同受体、Box、评分协议和参数下的本批次数值排序，不能证明真实结合或药效。

批量高级参数继承、record 级配体库导入、历史归档浏览、严格只读双归档比较与单归档 ZIP 导出属于当前源码增量。本批次不修改版本号，也不重新打包；正式安装包是否包含该功能，以 Release 页面为准。

## 当前源码：多配体共同对接（实验性）

多配体共同对接让两个配体在**同一次 Vina 搜索**中共同移动和优化。它不是串行批量筛选：

- 串行批量筛选：每个配体各自启动一次 Vina，每个任务有自己的分值和构象；
- 多配体共同对接：两个配体同时进入一次 Vina 搜索，每个 Mode 包含两个成员的一组联合构象。

使用条件：

- AutoDock Vina **1.2.0 或更高版本**，且运行时帮助必须声明多路径 `--ligand` 能力；
- 恰好两个已经准备好的 PDBQT 配体，输入顺序会保存为成员顺序；
- 刚性受体、显式 Box、全局对接；
- 评分函数只能选择 Vina 或 Vinardo。

操作时先在配体导入/配体库入口准备并选择两个 PDBQT，再在运行工作台显式切换到“多配体共同对接（实验性）”，核对成员顺序、受体、Box 与 Vina 参数后准备并开始任务。DockStart 调用 Vina 时使用一个 `--ligand` 参数，后面依次跟两个成员路径，例如：

```text
vina --receptor receptor.pdbqt --ligand ligand_a.pdbqt ligand_b.pdbqt --config box.txt --out combined_out.pdbqt
```

不要把它改写成两个独立的 `--ligand` 任务，也不要把已有多文件导入自动解释为共同对接。

结果解释：

- 每个 `MODEL` 是两个成员组成的一组联合构象；成员可以在 3D 视图中分别着色或隐藏，但仍属于同一个 Mode；
- 每个 Mode 只有一个联合评分。DockStart 不会把该分值拆成两个成员 affinity，也不会用单配体分值相加来制造成员评分；
- 不应直接比较成员组成或成员数量不同的联合任务；即使数值更低，也不能据此判断某个单独成员“贡献更好”；
- Vina stdout 的结果表行数可能多于最终写入 `out.pdbqt` 的 `MODEL` 数。某个 Mode 是否具有可查看构象，必须以 `out.pdbqt` 中实际存在且带结果记录的 `MODEL` 为准，不能只看 stdout 行号。

首版明确不支持柔性受体、AutoDock4/AD4Zn、任何预计算 maps、`score_only`、`local_only` 或水合对接，也不支持少于或多于两个成员。该协议复用已有 AutoDock Vina，不新增外部依赖或许可证组件。

这项能力目前只表示 v0.12.2 源码工作树中的实验性闭环。本轮不修改版本号、不重新打包，也不能据此声称现有 Release 已包含。2026-07-28 已用官方 5X72 双配体输入和仓库随附的 AutoDock Vina 1.2.7 完成源码级真实全链路：确认一个 `--ligand` 后跟两个有序路径、联合输出解析、两个成员分别加载、评分表和报告；官方参考输出（SHA256 `9fd1901bb52d0c767674fdd6e926f749e9995a35aa5b51e0318fd9e6f6922764`）也成功解析出 7 个 Mode、每个 Mode 两个成员块，最佳联合评分为 -19.043 kcal/mol。正式发布前仍需完成 Basic/Assisted 安装态、历史项目、失败恢复和 GUI 回归。

## V0.12.0 AutoDock4（maps）工作流

该功能面向已经理解 PDBQT、Box 和 AutoDock4 原子类型的用户，不替代默认 Vina 流程。

1. 自行安装 AutoGrid4 4.2.6，在“设置 → 工具路径”配置 `autogrid4.exe`。
2. 创建项目并准备受体、配体 PDBQT，在对接工作台设置并保存 Box。
3. 在“评分协议”选择 `AutoDock4（maps）`。
4. 检查 spacing、X/Y/Z 偶数点数、受体/配体原子类型；需要自定义参数库时选择参数文件。
5. 点击“生成并校验 maps”，或导入 `.maps.fld`。导入目录必须同时包含对应 GPF、受体来源文件及全部 map 文件。
6. maps 状态显示就绪后开始对接。DockStart 会把 maps 复制到 run 内，并在执行前复查路径和 SHA256。
7. AD4 项目汇总位于 `results/ad4_scores.csv`，报告位于 `reports/ad4_docking_report.md`。

受体或 Box 改变、map 文件被修改、当前配体出现未覆盖的原子类型时，旧 maps 会失效并阻止运行。
v0.12.0 只支持非金属刚性受体和单配体；Zn 体系、其他金属、柔性受体 AD4 与批量 AD4 不在当前范围。
AutoDock4、Vina 与 Vinardo 分值不可直接比较。

## 当前源码：Vina / Vinardo 预计算 maps

这项功能用于在受体、Box 和评分函数保持不变时保存并复用 Vina 或 Vinardo 网格。它不使用 AutoGrid4，也不是 AutoDock4 评分协议。

1. 准备刚性受体和一个配体，在工作台保存 Box，并将运行任务设为“全局对接”。
2. 保持评分协议为 `Vina / Vinardo`，在“网格来源”中选择“保存当前网格”。
3. 点击“生成并启用”。DockStart 会调用当前 Vina 的 `--write_maps`，校验 map header、数值数量与 SHA256，并用当前配体执行一次只评分兼容性探测。
4. 之后可在“实时计算”和“已保存 maps”之间切换。切回实时计算不会删除已有 map set；再次启用时仍会复核当前项目和配体。
5. 使用已保存 maps 准备 run 时，DockStart 会把全部 maps、manifest、受体溯源快照、配体和配置复制到 `runs/{run_id}/`，执行前后都核对冻结文件。

启用后的实际运行命令使用 `--maps`，不传 `--receptor`；Box、spacing、`no_refine` 和 `force_even_voxels` 也不再写入本次配置。该模式因此是 `grid-only`，等价于 `no-refine`，不能把结果解释为包含显式受体原子的最终精修。它与使用 `--receptor` 的普通 Vina/Vinardo 运行不是完全相同的计算语义。

DockStart manifest 会绑定：

- Vina 或 Vinardo 评分函数；
- 受体来源文件及 SHA256；
- 用户请求的 Box、Vina 实际生成的偶数体素网格和 spacing；
- Vina 版本、可执行文件大小和 SHA256；
- 每个 map 文件的 header、大小、SHA256 与整组 payload SHA256。

导入 DockStart manifest 时会校验上述证据。导入只有 `.map` 文件的外部目录时，用户还必须逐项确认受体、Box、评分函数和 Vina 来源；这些确认只记录来源声明，不能替代外部 maps 的独立科学证明。文件 header、数值数量、当前项目绑定和当前配体探测仍由 DockStart 检查。

当前源码边界：只支持刚性受体、单配体、全局对接；不支持柔性侧链、`score_only`、`local_only`、autobox、批量筛选或 AutoDock4/AD4Zn maps。受体、Box、评分函数、Vina 二进制或 map 内容变化时会阻止运行。该增量没有修改版本号，也没有重新打包；正式安装包能力仍以 Release 页面为准。

## 当前源码：AD4Zn beta

AD4Zn beta 是独立于标准 AutoDock4 maps 的 Zn 专用协议。它没有修改 Vina 的评分函数：DockStart 先准备带 TZ 伪原子的受体，使用 `AD4Zn.dat` 生成专用 AutoGrid maps，再由 Vina 以 `--maps <prefix> --scoring ad4` 运行；实际 docking 命令不再传入 `--receptor`。

使用前需要：

1. 自行安装 AutoGrid4 **4.2.7 或更高版本**并在设置页配置。标准 AD4 可用的 4.2.6 不满足 AD4Zn 门禁。
2. 准备项目受体和一个配体 PDBQT，并保存 Box。AD4Zn beta 只支持刚性受体、单配体、全局对接。
3. 从 AutoDock Vina v1.2.7 的[固定上游参考](https://github.com/ccsb-scripps/AutoDock-Vina/blob/v1.2.7/data/AD4Zn.dat)自行取得 `AD4Zn.dat`，在 AD4Zn 面板选择该文件。DockStart 不随仓库或安装包内置该文件，也不会静默联网下载。

操作流程：

1. 在 `AutoDock4` 协议中切换到 `AD4Zn beta`。
2. 查看识别出的 Zn 位点、附近配位原子和残基。只有恰好三个受体配位方向、并存在一个开放四面体方向的 Zn 位点才会生成一个 TZ；TZ 位于该开放方向上，距 Zn 约 2.0 Å。
3. 逐项确认研究目标 Zn、配位环境、质子化、水分子、辅因子、TZ 几何以及“本协议仅限 Zn”。确认记录会绑定当前受体 SHA256；替换受体后必须重新检查。
4. 选择并记录 `AD4Zn.dat`。DockStart 会检查 v1.2.7 受支持配置的关键自由能系数、Zn/TZ 参数及 GPL 声明，再复制到项目，记录本机来源路径、文件 SHA256、许可证 ID、支持配置和固定上游参考。文件字节与参考 SHA256 不同但关键配置一致时会保留实际哈希，不伪称来源已得到上游认证。
5. 准备 AD4Zn 受体并生成 maps。专用 GPF 会引用 TZ 受体和冻结的 `AD4Zn.dat`；只有 AutoGrid 日志出现成功完成、必需 maps 齐全且 manifest 校验通过时才会激活。
6. 通过运行前检查后开始对接。run 会保存原始受体、TZ 受体、参数文件、GPF、maps、AutoGrid/Vina 工具证据、用户确认和 SHA256。
7. AD4Zn 项目汇总写入 `results/ad4zn_scores.csv`，报告写入 `reports/ad4zn_docking_report.md`，不会覆盖标准 AD4 或 Vina/Vinardo 结果。

以下情况会直接阻止运行，不会静默降级为标准 AutoDock4：

- 没有 Zn、目标 Zn 的 4.5 Å 邻域存在其他金属、检测到非 Zn 金属却尝试套用本协议，或选定 Zn 不是恰好三个受体配位方向；
- Zn 与三个配位代表点近乎共面，开放方向的法向侧别不稳定；
- 未生成 TZ、TZ 几何或受体绑定失效；
- 未选择 `AD4Zn.dat`，关键参数或 GPL 声明不符合受支持配置，或项目副本/SHA256 与冻结记录不一致；
- AutoGrid4 低于 4.2.7、GLG 未成功完成、maps 缺失或被修改；
- 尝试启用柔性受体、批量筛选、`score_only`、`local_only` 或其他未支持组合。

AD4Zn beta 只处理 Zn，不应外推到 Mg、Fe、Ca 或其他金属。TZ 是用于 AD4Zn 网格势的几何伪原子，不是真实原子，也不证明配位构型正确。AD4Zn 分值不能与标准 AutoDock4、Vina 或 Vinardo 分值直接比较；官方 1S63 等基准、不同 Zn 环境、失败路径和干净安装态都必须进入正式发布验收。

项目中的 `ad4zn/parameters/AD4Zn.dat` 以及 run 快照是用户明确选择后生成的可追溯副本。若导出或分享整个项目，需要把该 GPL-2.0-or-later 资产视为再分发内容并保留相应许可证材料。

该能力目前只存在于源码工作树，尚未修改版本号、重新打包或进入正式 Release。现有 v0.12.0 Basic/Assisted 安装包不包含 AD4Zn beta；源码测试或单个基准通过也不能替代不同体系的人工结构审查与实验验证。
