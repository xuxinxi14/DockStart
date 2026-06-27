# OpenDesign 模板与设计系统筛选摘要

## 连接状态

- 检查日期：2026-06-27
- OpenDesign MCP：已连接，可用工具命名空间为 `mcp__open_design`
- 已执行检查：`list_projects`、`get_active_context`、`list_skills`、`list_plugins`、OpenDesign design-system resource 读取
- 当前限制：OpenDesign 没有激活项目，但可通过显式资源和插件目录完成筛选；本轮没有假装存在一个已打开模板项目。

## 搜索关键词

本轮按 DockStart 要求筛选以下方向：

- Enterprise
- Professional
- Minimal
- Dashboard
- Material
- GitHub
- Linear
- SaaS dashboard
- Developer tool
- Workspace
- Workbench
- Data dashboard
- Scientific dashboard
- Laboratory dashboard
- Bioinformatics dashboard
- Research workflow
- Admin console
- Command center

同时对 Cosmic、Glassmorphism、Luxury、Neon、Trading Terminal、Mission Control 等效果型或强主题方向做了排除性检查。

## 候选资源

### Professional

优点：
- 目标明确：可信、商务化、结构化。
- 适合作为 DockStart 的主气质：专业、克制、低噪声。
-  spacing scale 使用 4/8/12/16/24/32，和 DockStart 需要的 8px 节奏兼容。

问题：
- 默认黄色主色不适合科学软件，容易显得像商业后台或告警界面。
- 组件细节较泛，需要结合 GitHub/Ant/Material 补充文件、日志、状态和表单规则。

结论：保留为 Primary 气质参考，不直接采用黄色品牌色。

### Ant

优点：
- 企业应用、数据密集、表单和状态体系清晰。
- 适合工具链状态、运行前检查、文件状态卡、结果表格。
- 组件语义接近 DockStart 的真实任务：表单、校验、状态、表格。

问题：
- 容易变成普通 admin console。
- 如果照搬会显得过于后台模板化，需要更强工作流叙事。

结论：作为主组件结构参考。

### Material

优点：
- 表单、错误、状态、层级和响应式规范成熟。
- 适合新手引导、错误恢复、唯一主操作按钮。
- 可补足 DockStart 的可用性和 accessibility 基础。

问题：
- 默认紫色不适合 DockStart。
- 若使用过多 elevation 和大卡片，会继续保留 AI 生成感。

结论：作为 Secondary 交互和状态参考，颜色重做。

### Minimal

优点：
- 降低视觉噪声，适合科学工具的可信感。
- 有利于减少“卡片堆叠”和营销式空白。

问题：
- 过度 minimal 会让 workflow 引导不足。
- DockStart 需要状态密度，不能只靠留白。

结论：作为版式克制原则，不作为唯一系统。

### GitHub

优点：
- 文件、日志、状态、路径、版本、结果表格都很适配 DockStart。
- 14px 信息密度、hairline border、status pill 很适合桌面科学工具。
- 对“技术详情默认折叠”的处理有参考价值。

问题：
- 不能完全做成开发者平台，DockStart 用户包含新手学生。
- 默认白底/密度若直接照搬会过于工具化。

结论：用于文件路径、日志、状态、表格、技术详情。

### Linear

优点：
- 精确、安静、workflow 感强。
- Sidebar / issue workflow / active state 的层级值得参考。

问题：
- dark-mode-native 与 DockStart 当前目标不完全一致。
- 紫色/黑色氛围容易压过科学工具本身。

结论：只借 workflow 精确感、状态层级和信息收束，不采用暗色主视觉。

### IBM / Carbon

优点：
- 企业级严谨、强 token 化、表单和状态语义明确。
- 适合“规则集中定义”与“不要随意 margin/padding”。

问题：
- 0 radius 和强企业蓝过于硬，中文新手工作台会显得冷。

结论：借 token 化和表单纪律，不照搬视觉。

### Mission Control / Trading Terminal

优点：
- 状态明确、数据密集、运行/日志/工具链状态非常清晰。
- 命令中心式布局可启发 Viewer/Run 页面。

问题：
- 暗色、警戒色、终端密度过强，容易像监控大屏或金融终端。
- 会抢走 DockStart 作为科学教学工作台的可信度。

结论：只参考 operational clarity，不采用主风格。

### Warp / Vercel / Shadcn

优点：
- 开发者工具感强，技术细节处理克制。
- Shadcn/Vercel 的 6-8px radius、细边界、低阴影适合组件细节。

问题：
- Vercel/Shadcn 过于黑白极简，会削弱 DockStart 的工作流引导。
- Warp 暖黑和生活化影像不适合科学软件。

结论：少量借用按钮、边界、技术信息处理。

## 未选择花哨风格的原因

- Cosmic：科幻字体和未来感会让 DockStart 像演示玩具，不像可信科学工具。
- Glassmorphism：半透明和 blur 对路径、日志、表格可读性不利。
- Luxury：暗色大标题和高级品牌感会抢走工作台任务本身。
- Neon：高饱和主色和强效果会降低教学/科研软件的严肃度。
- Cyberpunk/Game dashboard：会误导用户认为 Viewer 或 docking score 有更强科学解释能力。

## 最终参考组合

Primary：
- Professional
- Ant

Secondary：
- Material
- Minimal

Reference details：
- GitHub：文件、日志、状态、路径、表格、技术详情。
- Linear：workflow 层级、sidebar active state、状态收束。
- IBM/Carbon：token 化、表单纪律、状态语义。

## 最终设计方向

DockStart V0.7 UI redesign 采用：

**Clean Scientific Workbench**

关键词：
- 专业
- 克制
- 清晰
- 现代
- 科学工具
- 低噪声
- 强引导
- 状态明确
- 工作流驱动

不是：
- 营销页
- 普通后台模板
- AI 生成卡片堆叠
- 指挥中心大屏
- 终端或游戏界面

视觉落地：
- 浅蓝灰背景
- 白色和轻微 elevated surface
- 深蓝灰正文
- 克制 cyan/blue accent
- 6-8px 圆角
- 1px hairline border
- 技术信息默认折叠
- 普通页面 max-width，Viewer 使用全宽 split workbench
