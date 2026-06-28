# Molecular Workbench Page Design

本文件定义 DockStart 方案 A：**Molecular Workbench / 分子工作台** 的页面系统。目标是在不新增科学功能的前提下，把现有功能组织成现代科研桌面软件。

## 一、AppShell 页面结构

### 左侧 Sidebar

分组：
- Project：总览、创建 / 打开项目。
- Workflow：获取结构、准备 Vina 输入、设置 Box、运行 Vina、查看结果。
- Workbench：3D 查看、实验记录。
- Support：工具链、文档帮助。

设计规则：
- Sidebar 表达任务，不表达 React 页面文件。
- 每个 item 有 label、短说明、状态 dot、disabled reason。
- 无项目时禁用需要项目的任务，但保留流程可见性。
- 当前任务用左侧 accent bar、浅色背景和深色文字表示。

### 顶部 Topbar

显示：
- 当前项目：项目名或“未加载项目”。
- 当前阶段：例如“准备 Vina 输入”。
- 工具链简况：Vina / Python 状态摘要。
- 版本：例如 `v0.7.x`。

禁止：
- “当前页面 / 当前项目”这类调试标签。
- 暴露 `ProjectCreatePage`、`ToolchainStatusPage` 等组件名。

### 中间 MainCanvas

用途：
- 当前任务的主操作区域。
- 普通页面使用 `--ds-content-max`。
- Dashboard 使用 max-width 与清晰 section。
- Viewer 使用全宽 workbench。

结构：
- PageHeader：任务名、说明、状态 pill。
- MainTask：唯一 primary action。
- SupportingContent：表单、状态、结果。

### 右侧 ContextPanel

用途：
- 下一步建议。
- 当前文件状态。
- 工具状态。
- 技术详情入口。

规则：
- ContextPanel 不承载主任务。
- 技术字段默认折叠。
- 错误状态必须给恢复建议。

## 二、Dashboard 设计

### 无项目态

Hero：
- 标题：开始一个 DockStart 项目。
- 说明：从数据库获取结构，或直接导入 PDBQT，完成一次 AutoDock Vina 对接。
- 主按钮：创建项目。
- 次按钮：打开已有项目。

快速流程：
1. 配置工具链。
2. 创建项目。
3. 获取结构 / 导入 PDBQT。
4. 运行并查看结果。

工具链状态提示：
- 小型状态条，提示 Vina / Python 是否需要配置。
- 不在无项目首页展示 manifest、sha256、resource_dir。

### 有项目态

Hero：
- 项目名。
- 当前阶段。
- 下一步建议。
- 主按钮：继续下一步。

模块：
- Workflow Timeline：项目、原始结构、Vina 输入、Box、对接运行、结果报告。
- Readiness Panel：缺什么、为什么阻塞、下一步按钮。
- Artifacts：raw files、prepared files、config、latest run、scores、report。
- Recent Run：最近运行状态和结果入口。
- Scientific Notice：小而清楚，不抢主操作。

设计目标：
- 首屏回答“我现在该干什么”。
- 不做同权重卡片墙。
- 路径和技术详情弱化。

## 三、工具链页设计

工具链页从状态 dump 改成配置向导。

三组主面板：

1. AutoDock Vina
   - 状态。
   - 来源：内置资源、用户配置路径、系统 PATH。
   - 版本。
   - 操作：配置路径 / 查看说明。

2. Python + RDKit + Meeko
   - Python 路径。
   - RDKit 状态。
   - Meeko 状态。
   - 操作：配置 Python / 查看 conda 指南。

3. Bundled resources
   - bundled Vina。
   - bundled Python。
   - manifest。
   - 技术详情折叠。

缺失时：
- 显示下一步建议，而不是只显示 raw stderr。
- `sha256`、manifest path、resource_dir、stderr 默认收进技术详情。

## 四、获取结构页设计

布局：
- receptor 和 ligand 双栏。
- 双栏都包含 raw 文件状态、下载表单、清除记录入口。
- 页面右侧显示下一步：准备 Vina 输入。

关键 notice：
- 原始结构文件不能直接运行 Vina。
- raw 文件只作为准备材料和来源记录。
- Vina 运行需要 prepared PDBQT。

主操作：
- 获取受体原始结构或获取配体原始结构。

错误恢复：
- RCSB/PubChem 失败时显示中文原因和重试建议。
- 网络或 ID 错误不暴露为主 UI 的 debug dump。

## 五、准备 PDBQT 页设计

布局：
- receptor preparation 面板。
- ligand preparation 面板。
- 工具链状态面板。
- 技术日志和 metadata 折叠。

主任务：
- 生成 Vina 输入文件，即 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`。

状态：
- raw 是否存在。
- prepared 是否存在。
- 上次 preparation 是否成功。
- Python / RDKit / Meeko 是否满足当前目标。

科学提示：
- 自动准备不保证质子化、电荷、缺失残基、水、金属、辅因子或链选择一定正确。
- 结果需要用户检查，不自动判断科学正确。

## 六、Viewer Workbench 设计

Viewer 不能像普通表单页，应使用专用工作台。

### 左侧 Inspector

包含：
- 结构来源。
- receptor / ligand / pose。
- run / mode。
- show/hide。
- box 开关。

### 中央 3D Canvas

包含：
- 3Dmol viewer。
- empty state。
- loading state。
- zoom to fit。
- reset view。
- show/hide box。

规则：
- Canvas 是视觉中心。
- 无文件时显示明确 empty state。
- 不能用装饰性假分子图替代真实 viewer。

### 右侧 Properties

包含：
- 文件信息。
- pose score。
- box 参数。
- warnings。
- scientific notice。

### 底部抽屉

包含：
- 技术日志。
- structure preview。
- metadata。

边界：
- Box 参数紧邻 viewer。
- pose score 只叫对接评分，不解释药效。
- 不做相互作用分析。
- 不做 pocket prediction。
- 不接 PLIP/ProLIF。

## 七、Vina run 页设计

流程：

```text
config -> prepare run -> execute -> analyze -> report
```

页面：
- VinaConfigPage：生成运行配置。
- RunPreparePage：创建 run 记录。
- RunExecutePage：开始对接运行。
- ResultPage：解析并查看 scores。
- ReportPage：导出实验记录。

共用规则：
- 使用 RunWorkflowBar。
- 每页一个 primary action。
- command preview、stdout、stderr、log 默认折叠。
- 错误显示恢复建议：检查 prepared 文件、Box、Vina 路径、config、run directory。

## 八、结果与报告页设计

### 结果页

内容：
- score summary。
- scores table。
- pose viewer 入口。
- run artifact 状态。

文案：
- best affinity 写作“最低对接评分”或“当前最低 score”。
- 不写“最好药效”“最有效分子”“证明结合”。

### 报告页

内容：
- 报告状态。
- 导出 Markdown 报告。
- 报告路径。
- scientific disclaimer。

文案：
- 报告是实验记录，不是科学结论。
- 必须保留：Docking score 仅供结构结合趋势参考，不能替代实验验证。

## 九、Help 页面设计

Help 应像工作台说明书，不像营销页。

模块：
- 5 分钟了解 DockStart。
- 完整流程。
- raw vs prepared。
- 工具链配置。
- 常见错误。
- 科学边界。
- 许可证边界。

规则：
- 优先回答“我该点哪里”和“为什么被阻塞”。
- 不重复长段技术细节。
- 外部工具许可证和科学限制要清楚。
