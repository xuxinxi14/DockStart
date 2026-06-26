# DockStart User Guide

本文档面向第一次使用 AutoDock Vina 和 DockStart 的用户，说明如何从已有 PDBQT 文件完成 MVP 流程，并说明 V0.2 raw workflow 与 V0.3 preparation 入口。

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

V0.4.0 已提供后端 viewer 读取接口，可以把项目内的 `raw/`、`prepared/` 和 `runs/{run_id}/out.pdbqt` 文本结构文件安全传给后续前端 viewer。当前阶段仍没有正式 3D 页面；V0.4.1 才会接入最小 3Dmol.js ViewerPage。

这些接口只读取文本结构文件，不调用 RDKit、Meeko 或 AutoDock Vina，不做相互作用分析，也不会判断 docking pose 是否代表真实结合或药效。超过 20 MB 的结构文件会被拒绝一次性读取，并返回中文结构化提示。
