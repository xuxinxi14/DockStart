# DockStart Smoke Tests

本文档记录 DockStart 当前三条可验收流程：

- V0.1：本地 prepared PDBQT docking 完整流程；
- V0.2：raw 原始结构下载、来源记录和 raw/prepared 边界检查。
- V0.3：raw → prepared PDBQT 自动准备入口和 docking 主线提示。

这些 smoke test 不验证 docking 科学结论，不判断药效，也不要求真实实验验证。

## V0.1 本地 PDBQT 完整流程 Smoke Test

### 测试目标

验证 V0.1 MVP 从项目创建到 Markdown 报告导出的完整链路，确认 DockStart 可以完成本地 prepared PDBQT docking 工作流的最小闭环。

### 前置条件

- 已安装 AutoDock Vina；
- 已有 `receptor.pdbqt`；
- 已有 `ligand.pdbqt`；
- DockStart 可以检测到 Vina；
- 本地有一个可写目录用于创建测试项目。

### 手动测试步骤

#### 1. 创建项目

在 DockStart 中创建一个新项目，例如：

```text
demo_project
```

预期：

- 项目目录被创建；
- `project.json` 存在；
- `raw/`、`prepared/`、`configs/`、`runs/`、`results/`、`reports/` 存在。

#### 2. 导入 PDBQT

导入：

- receptor: `receptor.pdbqt`
- ligand: `ligand.pdbqt`

预期：

```text
prepared/receptor.pdbqt
prepared/ligand.pdbqt
```

#### 3. 设置 Box

输入一组有效 box 参数，例如：

```text
center_x = 0
center_y = 0
center_z = 0
size_x = 20
size_y = 20
size_z = 20
```

预期：

- `project.json` 中 `box` 字段更新；
- 没有出现参数格式错误。

#### 4. 设置 Vina 参数

输入一组有效 Vina 参数，例如：

```text
exhaustiveness = 8
num_modes = 9
energy_range = 4
cpu = 0
seed = 留空或整数
```

预期：

- `project.json` 中 `vina` 字段更新；
- 没有出现参数格式错误。

#### 5. 生成 config

生成 Vina 配置文件。

预期：

```text
configs/vina_config.txt
```

文件应包含 receptor、ligand、box 和 Vina 参数。

#### 6. 准备 run

进入运行前检查并准备 run。

预期：

```text
runs/run_001/metadata.json
runs/run_001/command_preview.txt
runs/run_001/config_snapshot.txt
```

`metadata.json` 中 `status` 应为 `prepared`。

#### 7. 执行

执行 prepared run。

预期：

```text
runs/run_001/stdout.txt
runs/run_001/stderr.txt
runs/run_001/log.txt
runs/run_001/out.pdbqt
```

`metadata.json` 中：

```json
{
  "status": "finished",
  "exit_code": 0
}
```

如果 status 为 `failed`，查看 `stderr.txt` 和 `log.txt`。

#### 8. 解析

在结果页解析 Vina log。

预期：

```text
runs/run_001/scores.csv
results/scores.csv
```

`scores.csv` 表头应为：

```csv
mode,affinity_kcal_mol,rmsd_lb,rmsd_ub
```

#### 9. 导出报告

进入报告页导出 Markdown 报告。

预期：

```text
runs/run_001/docking_report.md
reports/docking_report.md
```

报告应包含：

- 项目信息；
- 输入文件；
- Box 参数；
- Vina 参数；
- 运行信息；
- Docking Score 结果；
- 免责声明。

### 预期输出清单

一次完整 V0.1 smoke test 至少应生成：

```text
prepared/receptor.pdbqt
prepared/ligand.pdbqt
configs/vina_config.txt
runs/run_001/metadata.json
runs/run_001/out.pdbqt
runs/run_001/log.txt
runs/run_001/scores.csv
results/scores.csv
reports/docking_report.md
```

推荐同时检查：

```text
runs/run_001/stdout.txt
runs/run_001/stderr.txt
runs/run_001/docking_report.md
```

### 通过标准

- run 状态为 `finished`；
- `scores.csv` 可以被结果页读取；
- Markdown 报告成功导出；
- 报告中没有药效判断；
- 报告包含 “Docking score 仅供结构结合趋势参考，不能替代实验验证。”。

## V0.2 Raw 下载流程 Smoke Test

### 测试目标

验证 V0.2 Structure acquisition line 可以下载并记录 raw 原始结构文件，同时确认 raw 文件不会被误当作 AutoDock Vina 可直接运行的 prepared PDBQT 输入。

### 前置条件

- 已创建 DockStart 项目；
- 项目目录可写；
- 当前环境可以访问 RCSB PDB 和 PubChem；
- 不要求安装 RDKit、Meeko、Open Babel、PLIP 或 MGLTools；
- 不要求配置 AutoDock Vina，因为本 smoke test 不执行 docking。

### 手动测试步骤

#### 1. 进入 raw 下载页面

在项目创建后进入“下载原始结构文件”页面。

预期：

- 页面显示当前项目路径；
- 页面明确提示当前只下载 raw 原始结构，不会自动生成 PDBQT；
- 页面提供进入 PDBQT 导入页的入口。

#### 2. 下载 receptor raw 文件

输入一个 PDB ID，例如：

```text
1HSG
```

选择 `pdb` 或 `cif` 格式，保持 overwrite 关闭，点击下载。

预期 raw 文件：

```text
raw/receptor_1HSG.pdb
```

或：

```text
raw/receptor_1HSG.cif
```

`project.json` 中 receptor 记录应包含：

```json
{
  "source": "rcsb_pdb",
  "source_id": "1HSG",
  "query_type": "pdb_id",
  "raw_file": "raw/receptor_1HSG.pdb"
}
```

如果选择 `cif`，`raw_file` 应对应 `.cif`。

#### 3. 下载 ligand raw 文件

使用 PubChem CID，例如：

```text
2244
```

点击下载 SDF。

预期 raw 文件：

```text
raw/ligand_2244.sdf
```

`project.json` 中 ligand 记录应包含：

```json
{
  "source": "pubchem",
  "source_id": "2244",
  "query_type": "cid",
  "raw_file": "raw/ligand_2244.sdf"
}
```

#### 4. 检查 raw 文件状态

点击重新加载 raw 状态。

预期：

- receptor raw 状态显示 `exists=true`；
- ligand raw 状态显示 `exists=true`；
- 文件大小大于 0；
- `record_consistent=true`；
- 页面显示 raw 文件路径、来源和下载记录。

#### 5. 验证 raw 不等于 prepared

检查项目中 prepared 文件状态。

预期 prepared 文件仍然需要用户手动提供：

```text
prepared/receptor.pdbqt
prepared/ligand.pdbqt
```

如果用户尚未导入 PDBQT，这两个文件可以不存在。raw 下载成功不应自动创建上述 PDBQT 文件。

#### 6. 导入 prepared PDBQT

用户使用外部流程自行准备 PDBQT 后，在 PDBQT 导入页导入：

```text
prepared/receptor.pdbqt
prepared/ligand.pdbqt
```

预期：

- raw 文件记录仍然保留；
- prepared PDBQT 文件被复制到 `prepared/`；
- `receptor.file` 和 `ligand.file` 指向 prepared PDBQT，而不是 raw PDB/CIF/SDF。

### 预期输出清单

V0.2 raw 下载流程至少应生成或记录：

```text
raw/receptor_{PDB_ID}.pdb
raw/receptor_{PDB_ID}.cif
raw/ligand_{cid}.sdf
```

其中 receptor 的 `.pdb` 和 `.cif` 通常二选一，取决于用户选择的格式。

prepared docking 输入仍然是：

```text
prepared/receptor.pdbqt
prepared/ligand.pdbqt
```

### 通过标准

- raw receptor 文件按 PDB ID 保存到 `raw/`；
- raw ligand SDF 按 CID 保存到 `raw/`；
- `project.json` 记录 raw 来源、查询类型和 raw 文件路径；
- raw 状态显示文件存在、大小和记录一致性；
- raw 文件不会覆盖 `receptor.file` / `ligand.file`；
- raw 下载不会自动生成 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`；
- 页面持续提示 raw 文件不能直接运行 AutoDock Vina。

## 当前边界

- raw 文件是原始结构或原始配体数据，不等于 prepared PDBQT。
- prepared PDBQT 才是 AutoDock Vina 当前运行流程需要的输入。
- DockStart V0.3.2/V0.3.3 可以在工具链可用时尝试把 ligand SDF/MOL 和 receptor PDB/CIF 准备为 PDBQT。
- DockStart 当前仍不自动把 MOL2/SMILES 转成 PDBQT。
- DockStart 当前不自动安装 RDKit/Meeko，也不保证自动准备结果科学正确。
- DockStart 当前仍不接入 Open Babel、PLIP 或 MGLTools。
- V0.3.4 只把 preparation 状态接入 config/run 前置检查和下一步建议，不修改 Vina 主流程。

## V0.3 Preparation 接入 Smoke Test

### 测试目标

验证 raw 文件已下载但 prepared PDBQT 缺失时，DockStart 会提示先进行 preparation；prepared PDBQT 存在后，原有 Vina config/run 流程继续可用。

V0.3.6 的自动准备 smoke test 使用 mock runner，不依赖真实 RDKit/Meeko，也不调用 AutoDock Vina。真实科学结果仍需要用户用实际工具和结构检查。

V0.3.8 增加真实工具链兼容性验收视角：如果当前解析到的 Python 缺少 RDKit 或 Meeko，验收应停在清晰的 `missing` 状态和中文安装/配置提示，不应继续执行 preparation，也不应生成空的 `prepared/*.pdbqt`。

### 手动测试步骤

1. 创建项目并下载 receptor raw PDB/CIF 与 ligand raw SDF。
2. 不导入 PDBQT，直接尝试生成 `configs/vina_config.txt`。
3. 预期：页面或后端返回中文结构化提示，说明已下载 raw receptor/ligand，但尚未准备 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`。
4. 进入 PreparationPage，查看下一步建议。
5. 如果本机 Python + RDKit + Meeko 能力可用，可尝试准备 ligand/receptor PDBQT；如果不可用，手动准备并导入 PDBQT。
6. prepared 两个文件都存在后，再生成 config、准备 run、执行 Vina。
7. 检查 preparation 记录目录，例如 `preparation/ligand_001/metadata.json` 和 `preparation/receptor_001/metadata.json`。

### 通过标准

- raw 文件不会被当作 Vina 输入。
- `receptor.file` 和 `ligand.file` 仍指向 prepared PDBQT。
- preparation 失败时提示查看日志。
- RDKit/Meeko 缺失时返回结构化中文错误，不自动安装依赖。
- 每次 preparation 都保留独立 metadata、stdout、stderr、command、input snapshot 和 output check。
- prepared PDBQT 补齐后，V0.1 config/run/parse/report 流程不被破坏。

## V0.3 当前仍不做

- Open Babel；
- MGLTools；
- PLIP；
- 3D 可视化；
- 相互作用分析；
- 分子动力学；
- PDF 报告；
- 药效判断。

V0.4 以后再考虑 3D 可视化、box 可视化设置、相互作用分析和批量 docking。
