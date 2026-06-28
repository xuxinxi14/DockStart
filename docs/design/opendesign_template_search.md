# OpenDesign 模板与设计系统筛选摘要

## 连接状态

- 检查日期：2026-06-28
- MCP 工具发现：Codex 能发现 `mcp__open_design` 工具命名空间。
- daemon 状态：不可达。`list_projects`、`get_active_context`、`list_skills`、`list_plugins` 均返回 `cannot reach the Open Design daemon at http://127.0.0.1:1141`。
- 结论：本轮不能声称已经通过 OpenDesign 实时搜索模板或读取模板项目。后续设计整理基于本地 DockStart 设计规范、已有 V0.7 文档、现代软件 UI 规范和用户指定的方案 A：Molecular Workbench。

## 原计划搜索关键词

如果 OpenDesign daemon 可用，应优先搜索：

- Enterprise
- Professional
- Minimal
- Dashboard
- Material
- GitHub
- Linear
- Workbench
- Developer tool
- Scientific dashboard
- Laboratory dashboard
- Research workflow
- Admin console

同时应避免或只做排除性检查：

- Cosmic
- Glassmorphism
- Luxury
- Creative
- Doodle
- Neon
- Cyberpunk
- Game dashboard

## 本轮候选设计系统判断

由于 OpenDesign daemon 未连接，以下候选不是实时搜索结果，而是用于 DockStart 的本地设计参考判断。

### Professional / Enterprise

适合 DockStart：
- 强调可信、结构化、低噪声，适合科研工作台。
- 适合表达工具链状态、流程阻塞、项目 readiness。
- 视觉层级可以通过边框、标题、状态 pill 和稳定 grid 建立。

不适合之处：
- 若照搬企业后台，会变成普通 admin console。
- 需要避免商业 SaaS 式大卡片和营销式 hero。

采用方式：
- 作为主气质参考：专业、冷静、可信。

### Minimal

适合 DockStart：
- 降低视觉噪声，避免 AI 生成感的卡片堆叠。
- 有利于突出唯一主任务和下一步。

不适合之处：
- 过度极简会削弱工作流引导。
- DockStart 需要状态密度，不能只靠留白。

采用方式：
- 用作留白、边框、阴影克制原则，而不是把界面做空。

### Material

适合 DockStart：
- 表单、错误、状态、disabled、loading 的交互规则成熟。
- 适合新手向工具链配置和任务页恢复建议。

不适合之处：
- 默认色彩和 elevation 不适合科学工作台。
- 过多浮起卡片会继续产生“卡片墙”问题。

采用方式：
- 借用状态语义、表单校验和错误恢复模式，不照搬 Material 视觉。

### GitHub

适合 DockStart：
- 文件、路径、日志、状态、版本、diff-like 技术详情处理非常契合 DockStart。
- 14px 信息密度、细边框、低阴影适合桌面科研工具。

不适合之处：
- 过于开发者化会让初学者觉得生硬。
- 不能把 DockStart 做成代码托管工具。

采用方式：
- 用于 FileChip、PathDisplay、LogDrawer、MetadataTable、状态摘要和技术详情折叠。

### Linear

适合 DockStart：
- workflow 层级清楚，active state 精确。
- Sidebar 和任务流有很强的方向感。

不适合之处：
- 暗色、紫色和任务管理语境不应成为 DockStart 主视觉。

采用方式：
- 借用导航层级、当前任务聚焦和状态收束。

### Scientific / Laboratory dashboard

适合 DockStart：
- 能表达实验步骤、结构数据、运行结果和限制条件。
- 适合把 Dashboard 定义为“项目驾驶舱”。

不适合之处：
- 很容易滑向监控大屏、炫酷可视化或科研海报。
- DockStart 不应把 docking score 包装成结论性科研发现。

采用方式：
- 用于 readiness、artifacts、timeline 和科学免责声明，而不是大面积图表装饰。

## 为什么不选择花哨风格

- Cosmic / Galaxy：会让 DockStart 像科幻演示，不像可信科研软件。
- Glassmorphism：透明、模糊和叠层会降低路径、日志、表格可读性。
- Luxury：高级品牌感和大标题会抢走工具任务本身。
- Neon / Cyberpunk：高饱和发光会削弱教学和科研可信度。
- Game dashboard / HUD：容易误导用户认为 Viewer 或 score 有更强解释能力。
- Creative / Doodle：轻松插画感不适合 AutoDock Vina 的严肃参数和文件工作流。

## 最终采用参考

Primary：
- Professional / Enterprise
- Scientific workbench

Secondary：
- Minimal
- Material

Detail references：
- GitHub：文件、日志、路径、表格、技术详情。
- Linear：工作流导航、active state、任务聚焦。
- Material：表单、错误恢复、disabled 和 loading 状态。

## 最终方向：Molecular Workbench

DockStart 采用 **DockStart Molecular Workbench / 分子工作台**。

理由：
- DockStart 的核心不是展示功能，而是帮助用户完成“结构获取 → Vina 输入准备 → Box → Vina 运行 → 结果 → 实验记录”的流程。
- 分子工作台能同时承载 3D Viewer、文件状态、工具链状态、日志和科学边界。
- 视觉应专业、冷静、可信、低噪声，强调状态判断和下一步，而不是装饰。

落地关键词：
- Clean
- Scientific
- Molecular
- Workbench
- Trustworthy
- Precise
- Calm
- Guided
- Low-noise
- Research-grade
