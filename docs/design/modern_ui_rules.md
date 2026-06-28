# DockStart 现代 UI 重设计规则

## 目标

DockStart 的 UI 目标是 Molecular Workbench：帮助初学者完成一次可复现 AutoDock Vina 对接，而不是展示炫技界面或继续堆功能。

本轮 UI redesign 不新增科学能力，不改变 Vina 算法，不接入 PLIP / ProLIF / Open Babel / MGLTools，不做相互作用分析或药效判断。

## 1. 系统状态可见

用户随时要知道三件事：

- 当前项目缺什么；
- 为什么被阻塞；
- 下一步点哪里。

规则：
- Dashboard 首屏必须给出下一步建议。
- 任务页必须展示当前状态 pill。
- 阻塞原因必须用中文说明，不只暴露 raw error。

## 2. 符合用户语言

主 UI 不显示组件名、内部字段名或 debug label。

禁止作为主文案出现：
- ProjectCreatePage
- ToolchainStatusPage
- StructureFetchPage
- current_environment
- metadata dump
- manifest dump
- 当前页面

这些内容只能出现在“技术详情”里。

## 3. 识别而非记忆

用户不应记住 raw、prepared、config、run、report 的关系。

规则：
- raw file 写作“原始结构文件”；
- prepared PDBQT 写作“Vina 输入文件”；
- config 写作“运行配置”；
- run 写作“对接运行”；
- report 写作“实验记录”。

每个页面都要显式说明输入、输出和下一步。

## 4. 一页一个主任务

每个任务页只能有一个最突出的 primary action。

示例：
- 获取结构页：获取结构；
- 准备页：准备 Vina 输入；
- 配置页：生成运行配置；
- 执行页：开始对接；
- 结果页：解析并查看 scores；
- 报告页：导出实验记录。

其他操作降级为 secondary 或 text action。

## 5. 技术详情默认折叠

路径、sha256、stdout、stderr、manifest、metadata、command preview 默认归入“技术详情”。

规则：
- 普通状态卡只显示结论和可行动建议。
- 技术细节使用 monospace，并允许横向/纵向滚动。
- 错误详情默认折叠，但错误标题和建议必须常显。

## 6. 导航基于工作流

Sidebar 表达任务流程，不表达 React page 列表。

分组：
- Project：总览、创建 / 打开项目；
- Workflow：获取结构、准备 PDBQT、设置 Box、运行 Vina、查看结果；
- Workbench：3D 查看、报告；
- Support：工具链、文档帮助。

## 7. 8px spacing scale

采用 4/8/12/16/24/32/48 的 spacing scale。

禁止：
- 随意 17px、23px、37px 之类的 margin/padding；
- 卡片套卡片造成层级混乱；
- 首屏大量同权重卡片。

## 8. 统一 token

颜色、字体、间距、圆角、边框、阴影必须在 CSS token 中集中定义。

禁止：
- 页面里临时发明高饱和色；
- 每个组件各自定义一套蓝色、灰色、圆角；
- 使用外部 CDN 字体或资源。

## 9. 主内容不能无限拉宽

普通页面使用 max-width，保证阅读和表单宽度稳定。

Viewer 工作台例外：
- 使用全宽 split layout；
- 中央 3D canvas 是视觉中心；
- Box 参数、pose score 和文件属性在两侧紧邻 viewer。

## 10. 科学边界统一表达

统一说明：
- docking score 不能证明药效；
- 自动 PDBQT preparation 不等于科学正确；
- Viewer 只做几何查看，不做相互作用分析；
- 报告是实验记录，不是科学结论。

每个相关页面都用小而明确的 ScientificNotice，不抢主操作。

## 11. 页面模板

任务页统一使用 StepTaskLayout 语义：

1. PageHeader：任务名、说明、状态 pill；
2. MainTask：唯一主操作和主要表单；
3. ContextPanel：文件状态、工具状态、下一步；
4. AdvancedDetails：路径、stdout/stderr、metadata，默认折叠。

## 12. 验收标准

通过 UI 审计时应确认：
- 组件名式标题已清理；
- raw/prepared/Vina/run/report 概念清楚；
- 技术路径不再压过主任务；
- 工具链页像配置向导，不像状态 dump；
- Viewer 像工作台，不像普通表单页；
- 所有原有功能入口仍可访问。
