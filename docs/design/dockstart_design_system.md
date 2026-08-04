# DockStart Design System

## 产品气质

**DockStart Instrument Console / 高端工业分子工作台**

DockStart 是现代分子建模与对接实验工作台，不是营销页、后台模板或游戏式 dashboard。默认暗色主题以深蓝应用壳和分级工作表面承载文件、参数、日志、表格与 3D viewer；亮色主题提供冷灰蓝日间工作环境。界面应专业、冷静、可信、低噪声、强引导、状态明确。

深蓝覆盖原则：
- 默认主题允许工作区使用深蓝分层，但应用壳、主面板、raised card、输入框和 Viewer 必须保持可辨认的亮度差。
- 亮色主题只改变应用表面，不改变科学 Viewer 的深色画布语义。
- 深色区域必须使用高对比文字，不使用灰字压在深蓝底上。
- 不使用渐变、霓虹、发光、玻璃拟态或装饰纹理。

完整主题 token 定义见 `docs/design/molecular_workbench_theme_tokens.md`。本文件说明这些 token 在 DockStart 前端组件中的使用方式。

## 色彩 token

| Token | 用途 |
| --- | --- |
| `--ds-bg-app` | 应用最外层冷灰蓝背景 |
| `--ds-bg-workspace` | 中央工作区背景 |
| `--ds-surface-panel` | MainPanel 与主要表单面板 |
| `--ds-surface-raised` | 内部状态、文件和参数表面 |
| `--ds-surface-input` | 输入框、下拉框和可编辑路径 |
| `--ds-surface-hover` | 行与次级控件 hover |
| `--ds-surface-selected` | 当前步骤和选中行 |
| `--ds-nav-bg` | Sidebar |
| `--ds-topbar-bg` | Topbar |
| `--ds-statusbar-bg` | Statusbar |
| `--ds-rail-bg` | RightRail / ContextPanel |
| `--ds-border-subtle` | 普通分隔线 |
| `--ds-border-default` | 默认 1px 边界 |
| `--ds-border-strong` | 重点分隔、active state |
| `--ds-text-strong` | 页面与面板标题 |
| `--ds-text-primary` | 主文字 |
| `--ds-text-secondary` | 正文和辅助说明 |
| `--ds-text-muted` | 元信息、disabled hint |
| `--ds-text-disabled` | 占位、禁用提示 |
| `--ds-text-on-dark*` | 深色结构区文字层级 |
| `--ds-brand` | 主行动和 focus |
| `--ds-brand-soft` | 当前步骤、信息提示背景 |
| `--ds-molecule` | 结构查看、受体/配体相关点缀 |
| `--ds-vina` | AutoDock Vina 流程点缀 |
| `--ds-success` | 已就绪、完成 |
| `--ds-warning` | 待确认、风险 |
| `--ds-danger` | 错误、失败、阻塞 |
| `--ds-info` | 中性信息、下一步建议 |

规则：
- 不在页面内临时发明颜色。
- 工作区、panel、raised 与 input 必须形成稳定的视觉层级；不能只靠阴影区分。
- 不使用纯白大面板、渐变大背景或重阴影。
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

## 动效与等待反馈

- 只使用 `--ds-motion-*` 与 `--ds-ease-*`；普通控件 120ms，页面单向进入 180ms。
- 页面切换只做一次 `opacity + 4px` 入场，不保留旧页面，不给 3D 分子或科学数值添加装饰动画。
- 约 230ms 内完成的页面加载或本地操作不显示 spinner，避免闪烁。
- 阻塞式 loading 只用于会写文件、转换或需要锁定输入的操作；在线检索优先使用内联状态。
- `prefers-reduced-motion` 下动画必须退化为单帧，信息不能只靠运动表达。

## 可访问性与桌面交互

- 图标按钮必须有稳定的 accessible name；折叠导航和窗口控件提供可聚焦 Tooltip。
- 页面切换后主内容回到顶部、获得程序化焦点并更新窗口标题。
- 日志、错误、路径、SHA256、命令和 Markdown 报告必须允许选择、右键与键盘复制。
- 模态加载关闭后恢复触发焦点；多层 overlay 使用引用计数管理 `inert` 与滚动锁。
- 同一颜色不能同时表示流程状态与科学结论；所有状态必须同时有文本。

## 响应式工作区

- 以 Windows 125% 缩放下的有效 CSS 宽度验收，不只按物理分辨率判断。
- 通用双栏、Run 与 Result 在约 1180px 时收起右侧检查栏，避免主任务区被压缩到约 500px。
- 960–980px 自动进入紧凑侧栏；此时不显示无效的手动折叠按钮。
- sticky 操作栏在窄屏恢复为普通文档流，不能遮挡正文或最后一个表单控件。

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
- 工作流摘要和项目记录更新时间。
- 工具链、帮助和主题快捷入口。

禁止“当前页面 / 当前项目”这种调试式标签。

### ProjectHeader / Project Dashboard

用于 Dashboard：项目名、当前阶段、下一步建议和主按钮。

### WorkflowRail / WorkflowTimeline

用于 Dashboard 和运行流程：顶层统一为结构准备、范围、运行、结果与报告四阶段；获取原始结构与转换 PDBQT 是结构准备的子状态。

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

前端组件应收敛到这些可复用 building blocks：

- `SectionHeader`
- `SectionCard`
- `StatusBadge`
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
- `Tooltip`
- `DelayedPending`
- `PageShell` / `PageHero` / `BodyGrid` / `MainPanel` / `RightRail`

样式所有权：

- `tokens.css` 只定义主题与尺度 token。
- `components.css` 是 Button、Status、EmptyState、Callout、Error 等共享原语的基础来源。
- `layout.css` 与 `instrument-console.css` 分别承载结构和应用壳主题；上下文覆盖必须带明确父级。
- 工作流样式由 `run-cockpit.css`、`workspace-console.css` 等功能文件负责，并在 `main.tsx` 统一声明导入顺序。
- 不在组件模块内隐式导入 CSS，不保留未进入构建链的备用主题文件。

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
