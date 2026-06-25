# DockStart V0.1 Smoke Test

## 测试目标

验证 V0.1 MVP 从项目创建到 Markdown 报告导出的完整链路，确认 DockStart 可以完成本地 PDBQT docking 工作流的最小闭环。

本 smoke test 不验证 docking 科学结论，不判断药效，也不要求真实实验验证。

## 前置条件

- 已安装 AutoDock Vina；
- 已有 `receptor.pdbqt`；
- 已有 `ligand.pdbqt`；
- DockStart 可以检测到 Vina；
- 本地有一个可写目录用于创建测试项目。

## 手动测试步骤

### 1. 创建项目

在 DockStart 中创建一个新项目，例如：

```text
demo_project
```

预期：

- 项目目录被创建；
- `project.json` 存在；
- `raw/`、`prepared/`、`configs/`、`runs/`、`results/`、`reports/` 存在。

### 2. 导入 PDBQT

导入：

- receptor: `receptor.pdbqt`
- ligand: `ligand.pdbqt`

预期：

```text
prepared/receptor.pdbqt
prepared/ligand.pdbqt
```

### 3. 设置 Box

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

### 4. 设置 Vina 参数

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

### 5. 生成 config

生成 Vina 配置文件。

预期：

```text
configs/vina_config.txt
```

文件应包含 receptor、ligand、box 和 Vina 参数。

### 6. 准备 run

进入运行前检查并准备 run。

预期：

```text
runs/run_001/metadata.json
runs/run_001/command_preview.txt
runs/run_001/config_snapshot.txt
```

`metadata.json` 中 `status` 应为 `prepared`。

### 7. 执行

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

### 8. 解析

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

### 9. 导出报告

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

## 预期输出清单

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

## 通过标准

- run 状态为 `finished`；
- `scores.csv` 可以被结果页读取；
- Markdown 报告成功导出；
- 报告中没有药效判断；
- 报告包含 “Docking score 仅供结构结合趋势参考，不能替代实验验证。”。
