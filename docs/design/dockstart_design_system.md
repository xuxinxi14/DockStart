# DockStart Design System

## 产品气质

**DockStart Molecular Workbench / 分子工作台**

DockStart 是现代分子建模与对接实验工作台，不是营销页、后台模板或游戏式 dashboard。界面应专业、冷静、可信、低噪声、强引导、状态明确，并为文件、日志、表格和 3D viewer 留出稳定空间。

完整主题 token 定义见 `docs/design/molecular_workbench_theme_tokens.md`。本文件说明这些 token 在 DockStart 前端组件中的使用方式。

## 色彩 token

| Token | 用途 |
| --- | --- |
| `--ds-bg-app` | 应用背景，浅冷灰蓝 |
| `--ds-bg-subtle` | Sidebar、次级区块、技术详情背景 |
| `--ds-bg-panel` | 主面板、表单、表格 |
| `--ds-bg-panel-soft` | 空状态、折叠详情、弱提示 |
| `--ds-bg-elevated` | Topbar、重点面板、Viewer 工具条 |
| `--ds-border-subtle` | 普通分隔线 |
| `--ds-border-default` | 默认 1px 边界 |
| `--ds-border-strong` | 重点分隔、active state |
| `--ds-text-primary` | 主文字 |
| `--ds-text-secondary` | 正文和辅助说明 |
| `--ds-text-muted` | 元信息、disabled hint |
| `--ds-text-faint` | 占位、低优先级时间戳 |
| `--ds-accent` | 主行动和 focus |
| `--ds-accent-soft` | 当前步骤、信息提示背景 |
| `--ds-molecule` | 结构查看、受体/配体相关点缀 |
| `--ds-vina` | AutoDock Vina 流程点缀 |
| `--ds-success` | 已就绪、完成 |
| `--ds-warning` | 待确认、风险 |
| `--ds-danger` | 错误、失败、阻塞 |
| `--ds-info` | 中性信息、下一步建议 |

规则：
- 不在页面内临时发明颜色。
- 分子 cyan 和 Vina 蓝紫只能作为语义点缀。
- 状态色只表达状态，不表达科学结论。

## 字体

UI font stack：

```css
var(--ds-font-ui)
```

Monospace：

```css
var(--ds-font-mono)
```

Type roles：
- H1：`--ds-title-lg`
- H2：`--ds-title-md`
- H3：`--ds-title-sm`
- Body：`--ds-text-md`
- Meta：`--ds-text-xs` / 600
- Caption：`--ds-text-xs`
- Code：`--ds-text-xs` 或 `--ds-text-sm` monospace

规则：
- UI 字体使用系统字体，不依赖外部 CDN。
- 路径、命令、日志、文件名使用 monospace。
- 路径和日志默认降低视觉权重，不压过主任务。

## 间距

Spacing scale：
- `--ds-space-1`: 4px
- `--ds-space-2`: 8px
- `--ds-space-3`: 12px
- `--ds-space-4`: 16px
- `--ds-space-5`: 24px
- `--ds-space-6`: 32px
- `--ds-space-7`: 48px

规则：
- 页面 section gap：24 或 32。
- card/panel padding：16 或 24。
- form gap：12 或 16。
- toolbar gap：8。
- 普通页面 max-width：`--ds-content-max`。
- Viewer workbench 使用全宽。

## 圆角

- `--ds-radius-sm`：按钮、输入框、标签。
- `--ds-radius-md`：表格容器和普通面板。
- `--ds-radius-lg`：重点面板和 Viewer 工作台。
- `--ds-radius-xl`：少数大 empty state，不作为默认卡片圆角。

不使用大圆角玩具化卡片，不使用玻璃拟态圆角浮层。

## 阴影

- `--ds-shadow-none`：默认。
- `--ds-shadow-soft`：轻微浮起，如 Topbar。
- `--ds-shadow-panel`：少数重点 panel 或浮层。

规则：
- 优先用 border 和 background 区分层级。
- 不做厚重卡片阴影。

## 组件层级

### AppShell

固定三段：
- Sidebar：工作流导航。
- Topbar：项目、阶段、工具链摘要、版本。
- MainCanvas：当前任务。

### Sidebar

按 Project / Workflow / Workbench / Support 分组。

每个 item：
- label。
- 一行说明。
- status dot。
- disabled reason。

### Topbar

显示：
- 项目名称或“未加载项目”。
- 当前工作流阶段。
- 工具链简要状态。
- 版本号。

禁止“当前页面 / 当前项目”这种调试式标签。

### ProjectHeader / Project Dashboard

用于 Dashboard：项目名、当前阶段、下一步建议和主按钮。

### WorkflowRail / WorkflowTimeline

用于 Dashboard 和运行流程：显示项目、原始结构、Vina 输入、Box、对接运行、结果报告。

### TaskCanvas

普通任务页主容器，限制宽度，分为主任务和上下文两列。

### ContextPanel

右侧或下方辅助信息：文件状态、工具状态、下一步、帮助入口。

### StatusPill / StatusCard

状态必须有语义色：
- ready / finished：success。
- missing / blocked / failed：danger。
- partial / warning：warning。
- running / optional：info 或 muted。

### FileChip / PathDisplay

路径和文件名用 monospace，默认弱化。完整路径进入技术详情。

### ActionButton

同页只出现一个 primary button。其他按钮降级为 secondary 或 text。

### EmptyState

无项目、无文件、无 pose 时使用。必须给出下一步按钮。

### ErrorRecoveryPanel

错误面板包含：
- 人能看懂的标题。
- 发生了什么。
- 建议怎么恢复。
- raw error 折叠。

### ScientificNotice

小型、低噪声、固定措辞。提醒 score、preparation、viewer 边界，不替代主任务。

### LogDrawer / AdvancedDetails

stdout、stderr、log、metadata、manifest、sha256、command preview 默认折叠显示。

## 基础组件文件

前端组件应逐步收敛到这些可复用 building blocks：

- `Card`
- `Panel`
- `SectionHeader`
- `StatusPill`
- `StatusCard`
- `FileChip`
- `PathDisplay`
- `ActionButton`
- `EmptyState`
- `Notice`
- `WarningCallout`
- `ErrorRecoveryPanel`
- `ScientificNotice`
- `WorkflowTimeline`
- `WorkflowStepper`
- `ContextPanel`
- `AdvancedDetails`

## 页面方向

Dashboard：
- 驾驶舱，不是卡片墙。
- 首屏回答“我现在该干什么”。

任务页：
- 一个主任务。
- 状态与下一步清楚。
- 技术细节折叠。

Viewer：
- 左 Inspector、中央 Canvas、右 Properties、底部技术抽屉。
- 3D canvas 是视觉中心。
- Box 参数紧邻 viewer。
- 不做相互作用分析或 pocket prediction。

工具链：
- 配置向导。
- Vina、Python + RDKit + Meeko、内置资源三组。
- sha256、manifest、resource_dir 默认折叠。
