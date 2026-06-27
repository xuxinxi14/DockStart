# CLAUDE.md

# DockStart
 Coding Agent 指南

## 1. 项目目标

你正在开发 DockStart，一个基于 AutoDock Vina 的第三方开源中文分子对接工作台。

目标不是开发新的 docking 算法，而是围绕 AutoDock Vina 构建现代化、中文化、可复现的图形化工作流。

产品定位已经从“外部工具调用器”调整为“开箱即用的一站式分子对接平台”。当前 V0.1 是 Lite MVP，依赖用户已有 PDBQT 和 Vina；后续 DockStart Full 应逐步实现分发简单、内置工具链、开箱即用、中文引导，并覆盖分子对接全过程。V0.2.3 已完成 bundled Python runtime 的路径解析、manifest 完整性检查和 ToolchainStatusPage 展示。V0.2.5 开始 Structure acquisition line，只下载 RCSB PDB / PubChem CID 原始结构并记录来源；V0.2.6 增强 raw 文件状态展示和 raw 记录管理；V0.2.7 增强 RCSB/PubChem raw 来源查询；V0.2.8 增强 raw/prepared 流程 UI 引导；V0.2.9 新增手动 PDBQT 准备指南；V0.2.10 整理 V0.1/V0.2 smoke test 和 release notes；V0.3.0 新增自动准备工作流模型和最小入口；V0.3.1 增强 RDKit/Meeko 能力检测；V0.3.2 已实现 ligand SDF/MOL 到 prepared/ligand.pdbqt 的最小自动准备；V0.3.3 已实现 receptor PDB/CIF 到 prepared/receptor.pdbqt 的最小自动准备；V0.3.4 已将 preparation 状态接入现有 config/run 前置检查和下一步建议；V0.3.5 已新增 preparation 审计记录；V0.3.6 已完成 preparation 文档和 mock smoke test 收尾；V0.3.7 已完成 preparation 冻结审计。当前仍未实现 MOL2/SMILES 自动准备、复杂结构修复或 Vina 主流程改造。

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

### 7.1 路径输入必须提供"选择"按钮

所有需要用户输入文件系统路径（文件或目录）的输入框，都必须在输入框旁提供一个“选择…”按钮，点击后调用 `tauri-plugin-dialog` 的原生对话框，让用户在电脑上选择路径，并把选中的绝对路径回填到输入框。用户仍可手动编辑路径。

规则：

* 目录类输入（项目保存目录、默认项目目录等）使用 `mode="directory"`。
* 文件类输入（receptor/ligand PDBQT、Vina/Python 可执行文件等）使用 `mode="file"`，并按需提供 `filters` 限定扩展名（例如 PDBQT 文件用 `[{ name: "PDBQT", extensions: ["pdbqt"] }]`）。
* 统一使用 `PathInput` 组件（`apps/desktop/src/components/PathInput.tsx`），不要在各页面重复实现对话框调用。
* 选完路径后仍允许手动修改；对话框只是辅助，不强制覆盖。
* 禁止只提供裸 `<input>` 让用户手敲绝对路径——这会让初学者出错。

接入要求（已就绪，新增页面时复用即可）：

* Rust：`Cargo.toml` 依赖 `tauri-plugin-dialog`；`main.rs` 注册 `.plugin(tauri_plugin_dialog::init())`。
* 权限：`src-tauri/capabilities/default.json` 已授权 `dialog:default`。
* 前端：`package.json` 依赖 `@tauri-apps/plugin-dialog`。

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

## 15. V0.1.11 当前状态

V0.1 MVP 已完成本地 PDBQT docking 最小闭环，包括结果解析、`scores.csv` 导出和 Markdown 报告导出。

当前优先级进入文档整理和 V0.2 准备。后续开发应先保持 V0.1 流程稳定，再评估 PDB/PubChem 下载、RDKit/Meeko 自动准备和更完善的错误引导。

除非用户明确要求并确认许可证边界，不要直接复制第三方源码或二进制到仓库。Open Babel、PLIP、MGLTools 等工具仍应作为外部可选集成来评估。

## 16. DockStart Full 工具链原则

后续涉及工具链时，默认遵守：

```text
内置工具 > 用户配置路径 > 系统 PATH
```

规划目录：

```text
resources/
├─ tools/
│  └─ vina/
├─ python/
└─ licenses/
```

架构边界：

* V0.1 Lite 仍依赖用户已有 PDBQT 和 Vina，不要为了文档定位调整而改业务逻辑。
* V0.2.0 优先评估内置 Vina。
* V0.2.1 设计 ToolchainStatusPage。
* V0.2.2 统一工具链资源路径与打包兼容。
* V0.2.3 已完成 bundled Python runtime resolution and integrity check。
* V0.2.4 是路线校准与工具链文档整理，不实现新功能。
* 后续可继续做离线 runtime 管理，但默认不提交二进制 runtime。
* V0.2.5 实现 PDB/PubChem 原始结构下载基础层，只保存 raw 文件并记录来源。
* V0.2.6 实现 raw 文件状态增强和 raw 记录清除，不改变 prepared PDBQT 文件。
* V0.2.7 实现 RCSB/PubChem raw 来源查询增强，SMILES 只返回暂未支持提示。
* V0.2.8 实现 raw/prepared 流程 UI 引导增强，不新增自动制备逻辑。
* V0.2.9 新增手动 PDBQT 准备指南，不新增自动制备逻辑。
* V0.2.10 整理 smoke test 与 release notes，不新增自动制备逻辑。
* V0.3.0 建立 raw → prepared PDBQT 自动准备模型和入口，但不执行真实制备。
* V0.3.4 将 preparation 状态接入现有 config/run 前置检查和下一步建议，不改变 Vina config、执行、解析或报告逻辑。
* V0.3.5 为每次 preparation 写入独立记录目录和 metadata/stdout/stderr/command/input/output 记录。
* V0.3.6 只做 preparation 文档收尾和 mock smoke test，不新增真实化学能力。
* V0.3.7 只做冻结审计、版本统一和文档校准，不新增功能。

Python runtime 当前解析优先级为：

```text
bundled > configured > current_environment
```

当前仓库只提交 `resources/python/README.md`，真实 runtime 文件（例如 `python.exe`、`Lib/`、`DLLs/`、`Scripts/`、`site-packages/`）被 `.gitignore` 忽略。`scripts/prepare_bundled_python.py` 只复制本地 Python runtime、计算 `python.exe` sha256、读取版本并更新 manifest；它不联网、不安装 Python 包、不安装 RDKit、不安装 Meeko。

Meeko/RDKit 当前已用于三类能力：V0.3.1 做 import、版本和准备能力检测；V0.3.2 可在 ligand raw 文件为 SDF/MOL 时，用已解析的 Python + RDKit + Meeko 尝试生成 `prepared/ligand.pdbqt`；V0.3.3 可在 receptor raw 文件为 PDB/CIF 且 Meeko receptor CLI 可发现时，尝试生成 `prepared/receptor.pdbqt`。V0.3.4 只在 config/run 前置检查中提示 raw 已有但 prepared 缺失、preparation 失败等状态。当前仍不做 MOL2/SMILES 自动准备或复杂结构修复。

当前明确未实现：

* ligand MOL2/SMILES 自动转 PDBQT；
* 复杂受体结构修复；
* Open Babel；
* PLIP/MGLTools；
* 3D 可视化；
* 药效判断。

许可证策略：

* Vina 可内置，但必须随包保留许可证文本、版本和来源说明。
* RDKit 可内置，但必须保留许可证和依赖说明。
* Meeko 可内置，但需要 LGPL 合规说明。
* Open Babel / MGLTools / PLIP 暂不进入核心内置包。

本轮或类似“文档和架构调整”任务不得顺手实现新功能、改 docking 逻辑或接入新工具。

## 17. V0.2.5 Structure Acquisition 边界

V0.2.5 只允许：

* 通过 RCSB PDB ID 下载受体原始结构到 `raw/`；
* 通过 PubChem CID 下载配体原始 SDF 到 `raw/`；
* 在 `project.json` 中记录 `source`、`source_id` 和 `raw_file`；
* 保留 `receptor.file` 和 `ligand.file` 作为 prepared PDBQT 路径。

V0.2.5 禁止：

* 自动转 PDBQT；
* 调用 RDKit 做分子处理；
* 调用 Meeko 做受体或配体准备；
* 接入 Open Babel、PLIP、MGLTools；
* 修改 Vina 运行流程；
* 做 3D 可视化或药效判断。

## 18. V0.2.6 raw 文件管理边界

V0.2.6 只允许：

* 展示 receptor/ligand raw 文件状态；
* 展示 raw 文件大小、修改时间、绝对路径和记录一致性；
* 在 overwrite=true 时允许重新下载覆盖同名 raw 文件；
* 清除 receptor/ligand 的 raw 记录；
* 可选删除项目 `raw/` 目录内对应 raw 文件；
* 保留 `receptor.file` 和 `ligand.file` 中的 prepared PDBQT 路径。

V0.2.6 禁止：

* 删除 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`；
* 自动转 PDBQT；
* 调用 RDKit 做分子处理；
* 调用 Meeko 做受体或配体准备；
* 接入 Open Babel、PLIP、MGLTools；
* 修改 Vina 运行流程；
* 做 3D 可视化或药效判断。

## 19. V0.2.7 结构来源查询边界

V0.2.7 只允许：

* RCSB PDB raw 下载支持 `pdb` / `cif`；
* PubChem CID 查询保持兼容；
* PubChem 名称查询下载 raw SDF；
* SMILES 查询返回中文结构化“暂未支持”提示；
* 在 `project.json` 中记录 `source`、`source_id`、`query_type`、`raw_file` 和 `downloaded_at`。

V0.2.7 禁止：

* 解析 SMILES；
* 自动生成 3D 构象；
* 自动转 PDBQT；
* 调用 RDKit 做分子处理；
* 调用 Meeko 做受体或配体准备；
* 接入 Open Babel、PLIP、MGLTools；
* 修改 Vina 运行流程；
* 做 3D 可视化或药效判断。

## 20. V0.2.8 raw/prepared UI 引导边界

V0.2.8 只允许：

* 首页展示 raw → prepared → Vina 的流程提示；
* ProjectCreatePage 展示 raw 下载和直接导入 PDBQT 两个入口；
* ImportPdbqtPage 解释 raw 文件和 prepared PDBQT 的区别；
* StructureFetchPage 下载后提示仍需手动准备 PDBQT；
* ToolchainStatusPage 在 V0.2.8 阶段说明 Meeko/RDKit 当时只做 import 检测。

V0.2.8 禁止：

* 自动生成 PDBQT；
* 调用 RDKit 做分子处理；
* 调用 Meeko 做受体或配体准备；
* 接入 Open Babel、PLIP、MGLTools；
* 做 3D 可视化；
* 修改 Vina 运行流程；
* 做药效判断。

## 24. V0.3.1 / V0.3.2 / V0.3.3 / V0.3.4 自动准备能力边界

V0.3.1 允许：

* 检测 RDKit import、版本和基础 SDF 读取能力；
* 检测 Meeko import、版本和候选 ligand/receptor preparation API 或 CLI；
* 返回 `unknown` 表示能力不可确认，不要硬写成功。

V0.3.2 允许：

* 在 `ligand.raw_file` 为 SDF 或 MOL 时准备 `prepared/ligand.pdbqt`；
* 使用已解析的 Python + RDKit + Meeko；
* 默认不覆盖已有 `prepared/ligand.pdbqt`；
* 保存 stdout、stderr 和 preparation log；
* 更新 `ligand.file` 和 `preparation.ligand`。

V0.3.3 允许：

* 在 `receptor.raw_file` 为 PDB 或 CIF 且 Meeko receptor CLI 可发现时准备 `prepared/receptor.pdbqt`；
* 使用已解析的 Python + Meeko；
* 默认不覆盖已有 `prepared/receptor.pdbqt`；
* 保存 stdout、stderr 和 preparation log；
* 更新 `receptor.file` 和 `preparation.receptor`。

V0.3.3 仍然禁止：

* MOL2 或 SMILES 自动准备；
* 复杂受体结构修复；
* 接入 Open Babel、PLIP、MGLTools；
* 做 3D 可视化；
* 修改 Vina 运行流程或 scoring function；
* 做药效判断。

V0.3.4 允许：

* 在 config/run 前置检查中提示 raw receptor/ligand 已有但 prepared PDBQT 缺失；
* 在 preparation failed 时提示查看 preparation 日志；
* 提供 `get_project_workflow_status(project_dir)` 和最小下一步建议；
* 保持旧项目兼容。

V0.3.4 禁止：

* 修改 Vina config 核心语义；
* 修改 Vina 执行逻辑；
* 修改 score 解析逻辑；
* 新增 Open Babel、PLIP、MGLTools；
* 新增 3D 可视化、相互作用分析或药效判断。

V0.3.5 允许：

* 为 ligand/receptor preparation 生成独立审计目录；
* 写入 `metadata.json`、`stdout.txt`、`stderr.txt`、`command.json`、`input_snapshot.json` 和 `output_check.json`；
* 更新 `project.json` 的 `latest_preparation`；
* 失败时保留 metadata 和日志。

V0.3.5 禁止：

* 调用 Vina；
* 解析 docking 结果；
* 把 preparation 记录解释为科学验证；
* 接入 Open Babel、PLIP、MGLTools；
* 做 3D 可视化、相互作用分析或药效判断。

V0.3.6 允许：

* 更新用户指南、smoke test、FAQ、manual PDBQT preparation、roadmap、README、PROJECT 和 CLAUDE；
* 用 mock preparation runner 验证 raw → prepared → config 的后端状态链路；
* 记录自动准备的科学限制和后续 V0.4 方向。

V0.3.6 禁止：

* 新增真实化学处理能力；
* 接入 Open Babel、MGLTools、PLIP；
* 做 3D 可视化、相互作用分析、分子动力学、PDF 报告或药效判断。

## 21. V0.2.9 手动 PDBQT 准备指南边界

V0.2.9 只允许：

* 新增 `docs/manual_pdbqt_preparation.md`；
* 解释 raw 文件、prepared PDBQT 和 Vina 输入要求；
* 说明 Meeko、AutoDockTools/MGLTools、Open Babel 等可选外部工具；
* 说明 Open Babel、MGLTools、PLIP 当前不内置；
* 说明 DockStart 当前不保证外部工具生成的 PDBQT 科学正确性。

V0.2.9 禁止：

* 自动生成 PDBQT；
* 调用 RDKit 做分子处理；
* 调用 Meeko 做受体或配体准备；
* 接入 Open Babel、PLIP、MGLTools；
* 做 3D 可视化；
* 修改 Vina 运行流程；
* 做药效判断。

## 22. V0.2.10 smoke test 与 release notes 边界

V0.2.10 只允许：

* 整理 `docs/smoke_test.md`；
* 区分 V0.1 本地 prepared PDBQT 完整流程和 V0.2 raw 下载流程；
* 说明 raw 预期产物：`raw/receptor_{PDB_ID}.pdb` 或 `.cif`，以及 `raw/ligand_{cid}.sdf`；
* 说明 prepared 预期产物：`prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`；
* 更新 README、roadmap 和 CHANGELOG；
* 同步版本号。

V0.2.10 禁止：

* 自动生成 PDBQT；
* 调用 RDKit 做分子处理；
* 调用 Meeko 做受体或配体准备；
* 接入 Open Babel、PLIP、MGLTools；
* 做 3D 可视化；
* 修改 Vina 运行流程；
* 做药效判断。

## 23. V0.3.0 自动准备工作流模型边界

V0.3.0 允许：

* 新增 `preparation` 数据模型；
* 新增准备状态读取、前置检查和重置；
* 检测 Python/RDKit/Meeko 状态；
* 新增最小 PreparationPage 入口；
* 保持旧项目兼容。

V0.3.0 禁止：

* 真实生成 ligand PDBQT；
* 真实生成 receptor PDBQT；
* 调用 RDKit 做配体处理；
* 调用 Meeko 做受体或配体准备；
* 接入 Open Babel、PLIP、MGLTools；
* 做 3D 可视化；
* 修改 Vina 运行流程；
* 做药效判断。
## V0.4 Viewer Boundary

DockStart V0.4 viewer is a minimal workflow aid. It can display project-local raw/prepared structures, synchronize the existing Box parameters, and inspect docking pose modes from `out.pdbqt`.

Do not describe the viewer as an interaction-analysis, pocket-prediction, drug-efficacy, or scientific-validation tool. Do not add PLIP, ProLIF, Open Babel, MGLTools, molecular dynamics, or scoring changes as part of the V0.4 viewer line.

V0.4.6 is a freeze audit stage. Only version consistency, documentation accuracy, missing tests, small type issues, and unclear Chinese messages may be fixed. Do not add new viewer features or refactor the frontend workflow during this stage.

## V0.5 Frontend Workflow Boundary

V0.5 is the frontend workflow overhaul line. It includes AppShell, ProjectDashboardPage, workflow stepper, shared status/error/disclaimer components, raw/preparation page cleanup, ViewerPage workspace cleanup, Vina run workflow bar, HelpPage, and onboarding.

V0.5.8 is a freeze audit stage. Only version consistency, documentation accuracy, small type/layout/copy bugs, and missing frontend wiring may be fixed.

Do not treat V0.5 as interaction analysis. Do not add PLIP, ProLIF, Open Babel, MGLTools, pocket prediction, drug-efficacy judgement, external CDN resources, Vina algorithm/scoring changes, large structure files, real docking outputs, or Python runtime binaries.

## V0.6 Release Packaging Boundary

V0.6 is the release engineering line. It covers Windows packaging, release scripts, bundled toolchain distribution strategy, first-run toolchain guidance, GitHub Release materials, and local installer validation.

V0.6.0 only establishes release structure and documentation:

```text
docs/release/release_strategy.md
docs/release/windows_packaging.md
docs/release/release_checklist.md
resources/vina/README.md
```

Allowed in V0.6:

* Tauri Windows build and installer workflow;
* release checklist and artifact hash scripts;
* bundled Vina and bundled Python runtime preparation and integrity checks;
* first-run guidance for configuring Vina and Python/RDKit/Meeko;
* local installer build validation without committing installer artifacts.

Forbidden in V0.6:

* new scientific features;
* PLIP / ProLIF / Open Babel / MGLTools integration;
* interaction analysis, pocket prediction, molecular dynamics, or drug efficacy judgement;
* AutoDock Vina algorithm or scoring changes;
* external CDN resources;
* committed installers, `dist`, `target`, real docking outputs, third-party source archives, `python.exe`, `Lib`, `DLLs`, `site-packages`, or conda environments;
* disabling SSL verification or force pushing.
