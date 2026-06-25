# CLAUDE.md

# DockStart
 Coding Agent 指南

## 1. 项目目标

你正在开发 DockStart，一个基于 AutoDock Vina 的第三方开源中文分子对接工作台。

目标不是开发新的 docking 算法，而是围绕 AutoDock Vina 构建现代化、中文化、可复现的图形化工作流。

第一阶段目标是实现最小闭环：

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

不要扩展到分子动力学、大规模虚拟筛选、AI 药效预测或自动论文生成。

## 2. 工作方式

每次修改前必须先阅读：

* PROJECT.md
* CLAUDE.md
* README.md，如果存在
* 当前相关源码文件

每次任务开始前先判断：

1. 当前功能属于 MVP、第二阶段还是第三阶段？
2. 是否会引入新的外部依赖？
3. 是否会影响许可证策略？
4. 是否会改变项目文件结构？
5. 是否会影响用户已有项目数据？

如果任务超出 MVP，除非用户明确要求，否则先只设计接口或 TODO，不要直接实现复杂功能。

## 3. 编码原则

### 3.1 不硬编码路径

禁止硬编码：

```text
C:\...
/Users/...
/home/...
```

所有外部工具路径必须来自：

* 用户设置；
* 环境变量；
* 自动检测结果；
* 项目配置文件。

### 3.2 不把科研工具写死在业务逻辑里

必须通过 adapter 调用外部工具。

推荐结构：

```text
backend/
├─ dockstart_core/
├─ adapters/
│  ├─ vina_adapter.py
│  ├─ meeko_adapter.py
│  ├─ rdkit_adapter.py
│  ├─ openbabel_adapter.py
│  └─ pubchem_adapter.py
├─ workflows/
└─ tests/
```

adapter 必须尽量提供：

```python
detect()
get_version()
validate_input()
run()
parse_output()
```

### 3.3 所有命令行调用必须安全

调用外部命令时：

* 使用参数数组，不拼接 shell 字符串；
* 检查文件是否存在；
* 检查输出目录是否可写；
* 捕获 stdout；
* 捕获 stderr；
* 保存完整日志；
* 返回结构化错误。

不要使用危险写法：

```python
os.system("vina --config " + config_path)
```

推荐使用：

```python
subprocess.run([...], capture_output=True, text=True)
```

### 3.4 错误信息必须中文友好

不要只显示：

```text
file not found
```

应该显示：

```text
没有找到受体 PDBQT 文件。请回到“受体准备”步骤，确认 receptor.pdbqt 是否存在。
```

错误对象建议包含：

```json
{
  "code": "RECEPTOR_FILE_NOT_FOUND",
  "title": "没有找到受体文件",
  "message": "没有找到 receptor.pdbqt，请检查文件路径。",
  "raw_error": "...",
  "suggestion": "请重新选择受体文件，或回到受体准备步骤。"
}
```

### 3.5 文件写入必须可追踪

每次运行 docking 必须保存：

* 输入文件路径；
* 配置文件；
* Vina 版本；
* 命令行参数；
* stdout；
* stderr；
* log.txt；
* metadata.json；
* 开始时间；
* 结束时间；
* exit code。

## 4. MVP 页面

第一阶段只实现这些页面：

```text
HomePage
ToolCheckPage
ProjectCreatePage
ImportPdbqtPage
BoxSetupPage
ParameterPage
RunPage
ResultPage
ReportPage
SettingsPage
```

不要一开始做复杂 dashboard。

## 5. MVP 后端能力

第一阶段后端只需要：

```text
检测 vina
检测 python
检测 meeko
检测 rdkit
读取项目配置
保存项目配置
生成 vina_config.txt
运行 vina
解析 vina log
导出 scores.csv
导出 docking_report.md
```

不要第一阶段实现：

* PDB 下载；
* PubChem 下载；
* Meeko 自动准备受体；
* Meeko 自动准备配体；
* PLIP；
* ProLIF；
* fpocket；
* 批量 docking。

可以预留接口，但不要把 MVP 做复杂。

## 6. 数据模型

项目配置文件建议为：

```text
project.json
```

基本结构：

```json
{
  "project_name": "demo_project",
  "created_at": "",
  "updated_at": "",
  "receptor": {
    "source": "local",
    "file": "prepared/receptor.pdbqt"
  },
  "ligand": {
    "source": "local",
    "file": "prepared/ligand.pdbqt"
  },
  "box": {
    "center_x": 0,
    "center_y": 0,
    "center_z": 0,
    "size_x": 20,
    "size_y": 20,
    "size_z": 20
  },
  "vina": {
    "exhaustiveness": 8,
    "num_modes": 9,
    "energy_range": 4,
    "cpu": 8,
    "seed": 12345
  },
  "runs": []
}
```

每次运行生成：

```text
runs/run_001/metadata.json
```

结构：

```json
{
  "run_id": "run_001",
  "started_at": "",
  "finished_at": "",
  "status": "finished",
  "vina_version": "",
  "command": [],
  "config_file": "configs/vina_config.txt",
  "output_file": "runs/run_001/out.pdbqt",
  "log_file": "runs/run_001/log.txt",
  "exit_code": 0,
  "best_affinity": null
}
```

## 7. UI 设计要求

整体 UI 应该专业、克制、清晰，不要做花哨游戏风。

用户对象是：

* 生物技术学生；
* 药学/生信初学者；
* 第一次使用 AutoDock Vina 的用户；
* 课程设计/本科毕设用户。

每个页面都要回答三个问题：

1. 当前这一步要做什么？
2. 为什么要做？
3. 做错了怎么办？

## 8. 中文术语建议

统一使用以下中文：

```text
receptor：受体
ligand：配体
docking box：对接箱体
pose：构象
affinity：结合能 / 亲和力评分
exhaustiveness：搜索彻底程度
num_modes：输出构象数量
energy_range：能量范围
seed：随机种子
prepared file：准备后的文件
raw file：原始文件
```

不要在 UI 中混用多个翻译。

## 9. 科学表达限制

禁止写：

```text
该分子一定有效
该药物可以治疗某疾病
该结果证明存在真实结合
该 docking 分数证明药效
```

可以写：

```text
该结果提示该配体在当前结构和参数下具有较低的 docking score。
该结果仅供后续实验或进一步计算参考。
```

报告中必须包含：

```text
Docking score 仅供结构结合趋势参考，不能替代实验验证。
```

## 10. 许可证限制

不要直接复制第三方项目源码到本项目，除非用户明确要求并确认许可证兼容。

不要默认内置 GPL 工具。

以下工具只能作为外部可选工具处理：

* Open Babel
* PLIP
* MGLTools

如果新增依赖，必须更新：

```text
docs/license_notes.md
```

并说明：

* 工具名称；
* 用途；
* 许可证；
* 集成方式；
* 是否内置；
* 是否需要用户自行安装。

## 11. 测试要求

每个核心函数都要有最小测试：

* 配置文件生成测试；
* Vina log 解析测试；
* project.json 读写测试；
* 路径不存在错误测试；
* 工具检测测试。

不要求第一阶段做完整端到端真实 docking 测试，但至少要提供 mock log 测试。

## 12. 不要做的事情

除非用户明确要求，不要做：

* 重写 AutoDock Vina；
* 修改 scoring function；
* 加入 AI 聊天助手；
* 加入自动论文生成；
* 加入分子动力学；
* 加入大型数据库虚拟筛选；
* 加入登录系统；
* 加入云同步；
* 加入商业支付；
* 加入复杂插件市场。

## 13. 每次完成任务后的输出格式

每次修改完成后，向用户汇报：

```text
完成了什么
修改了哪些文件
如何运行/测试
还有哪些风险或 TODO
```

不要只说“已完成”。

## 14. 当前优先级

当前最高优先级：

1. 搭建项目结构；
2. 实现工具检测；
3. 实现 PDBQT 导入；
4. 实现 box 参数表单；
5. 实现 Vina config 生成；
6. 实现 Vina 运行；
7. 实现 log 解析；
8. 实现结果表格；
9. 实现 Markdown 报告导出。

只有这些跑通后，才考虑 PDB/PubChem/Meeko/RDKit 自动准备流程。
