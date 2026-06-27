# DockStart Design System

## 产品气质

**Clean Scientific Workbench**

DockStart 是现代科学工作台，不是营销页、后台模板或游戏式 dashboard。界面应低噪声、强引导、状态明确，并为文件、日志、表格和 3D viewer 留出稳定空间。

## 色彩 token

| Token | 用途 |
| --- | --- |
| background | 应用背景，浅蓝灰 |
| surface | 主面板、表单、表格 |
| surface-elevated | 顶栏、重点面板、Viewer 工具条 |
| border | 默认 1px 边界 |
| border-strong | 重点分隔、active state |
| text-primary | 主文字 |
| text-secondary | 辅助文字 |
| text-muted | 元信息、disabled hint |
| accent | 主行动和 focus |
| accent-soft | 当前步骤、信息提示背景 |
| success | 已就绪、完成 |
| warning | 缺失、需确认 |
| danger | 错误、失败 |
| info | 中性信息 |
| molecule | 结构查看相关点缀 |
| vina | AutoDock Vina 流程点缀 |

推荐取值见 `apps/desktop/src/styles/tokens.css`。

## 字体

UI font stack：

```css
Inter, "Segoe UI", "Microsoft YaHei", "PingFang SC", Arial, sans-serif
```

Monospace：

```css
"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace
```

Type roles：
- H1：28px / 600
- H2：18px / 600
- H3：15px / 600
- Body：14px / 400
- Meta：12px / 600
- Caption：12px / 400
- Code：12-13px monospace

## 间距

Spacing scale：
- 4
- 8
- 12
- 16
- 24
- 32
- 48

规则：
- 页面 section gap：24px；
- card padding：16-20px；
- form gap：12-16px；
- toolbar gap：8px；
- 普通页面 max-width：1240px；
- Viewer workbench 使用全宽。

## 圆角

- small：4px，用于小标签、代码块、toolbar micro control；
- medium：6px，用于按钮、输入框、表格容器；
- large：8px，用于面板和 repeated cards。

不使用大圆角卡片，不使用玻璃拟态圆角浮层。

## 组件层级

### AppShell

固定三段：
- Sidebar：工作流导航；
- Topbar：项目、阶段、工具链摘要、版本；
- TaskCanvas：当前页面。

### Sidebar

按 Project / Workflow / Workbench / Support 分组。

每个 item：
- label；
- 一行说明；
- status dot；
- disabled reason。

### Topbar

显示：
- 项目名称或“未加载项目”；
- 当前工作流阶段；
- 工具链简要状态；
- 版本号。

禁止“当前页面 / 当前项目”这种调试式标签。

### ProjectHeader

用于 Dashboard：项目名、当前阶段、下一步建议和主按钮。

### WorkflowRail

用于 Dashboard 和运行流程：显示项目、原始结构、Vina 输入、Box、对接运行、结果报告。

### TaskCanvas

普通任务页主容器，限制宽度，分为主任务和上下文两列。

### ContextPanel

右侧或下方辅助信息：文件状态、工具状态、下一步、帮助入口。

### StatusPill / StatusCard

状态必须有语义色：
- success：已就绪；
- warning：缺失或待确认；
- danger：失败；
- info：可继续；
- muted：未知或未开始。

### FileChip / PathField

路径和文件名用 monospace，默认弱化。完整路径进入技术详情。

### ActionButton

同页只出现一个 primary button。其他按钮降级为 secondary 或 text。

### EmptyState

无项目、无文件、无 pose 时使用。必须给出下一步按钮。

### ErrorRecoveryPanel

错误面板包含：
- 人能看懂的标题；
- 发生了什么；
- 建议怎么恢复；
- raw error 折叠。

### ScientificNotice

小型、低噪声、固定措辞。提醒 score/preparation/viewer 边界，不替代主任务。

### LogDrawer

stdout、stderr、log、metadata、manifest、sha256、command preview 默认折叠显示。

## 页面方向

Dashboard：
- 驾驶舱，不是卡片墙；
- 首屏回答“我现在该干什么”。

任务页：
- 一个主任务；
- 状态与下一步清楚；
- 技术细节折叠。

Viewer：
- 左 Inspector、中央 Canvas、右 Properties、底部技术抽屉；
- 3D canvas 是视觉中心；
- Box 参数紧邻 viewer；
- 不做相互作用分析或 pocket prediction。

工具链：
- 配置向导；
- Vina、Python + RDKit + Meeko、内置资源三组；
- sha256、manifest、resource_dir 默认折叠。
