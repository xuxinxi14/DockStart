# DockStart Molecular Workbench Theme Tokens

## 一、主题命名

主题名称：**DockStart Instrument Console**

中文名：**高端工业分子工作台**

一句话定义：

DockStart 的视觉风格应像一台现代分子建模与对接实验仪器：深蓝应用壳负责建立稳定边界，冷灰蓝工作台承载参数、文件和表格。整体专业、冷静、可信、低噪声，以流程引导和状态判断为核心，而不是炫酷装饰。

## 二、主题关键词

- Clean：干净，减少无意义装饰和重复说明。
- Scientific：科研感，强调可复现、状态、边界和记录。
- Molecular：分子建模感，允许少量 cyan/teal 作为结构相关点缀。
- Workbench：工作台感，优先支持操作、检查、运行和复核。
- Trustworthy：可信，信息来源和状态要明确。
- Precise：精确，文件、参数、日志、表格要清楚。
- Calm：冷静，避免高饱和和强动效。
- Guided：强引导，用户随时知道下一步。
- Low-noise：低视觉噪声，技术细节默认折叠。
- Research-grade：不夸张宣传，不把 score 写成科学结论。

## 三、色彩 token

### 基础背景

```css
--ds-bg-app: #D2DBE4;
--ds-bg-workspace: #D7E0E8;
--ds-surface-panel: #EDF1F5;
--ds-surface-raised: #F5F7F9;
--ds-surface-input: #F8FAFB;
--ds-surface-hover: #E7EDF3;
--ds-surface-selected: #DCE8F3;

--ds-nav-bg: #041B30;
--ds-topbar-bg: #051D34;
--ds-statusbar-bg: #03182B;
--ds-rail-bg: #082846;
--ds-rail-bg-hover: #0B3155;
```

使用场景：
- `--ds-bg-workspace`：中央页面画布，必须是三层浅色系统中最暗的一层。
- `--ds-surface-panel`：MainPanel 与主要表单面板。
- `--ds-surface-raised`：面板内部的文件、状态和参数分组。
- `--ds-surface-input`：输入框、下拉框和可编辑路径。
- `--ds-nav-bg / topbar-bg / statusbar-bg`：同系但略有差异的应用结构色。
- `--ds-rail-bg`：RightRail 与上下文检查区。

亮度顺序必须保持：`workspace < panel < raised < input`。禁止大面积纯白。

### 边框

```css
--ds-border-strong: #9EACBA;
--ds-border-default: #B6C2CE;
--ds-border-subtle: #CAD3DC;
--ds-divider: #C5CED7;
```

使用场景：
- subtle：普通分隔线。
- default：面板、输入框、表格边界。
- strong：active state、关键分隔、viewer workbench 边界。

### 文字

```css
--ds-text-strong: #102337;
--ds-text-primary: #263C52;
--ds-text-secondary: #53687D;
--ds-text-muted: #718295;
--ds-text-disabled: #97A5B3;
--ds-text-on-dark: #F4F7FA;
--ds-text-on-dark-secondary: #C2CFDB;
--ds-text-on-dark-muted: #8FA4B7;
```

使用场景：
- strong：页面标题、面板标题和关键数据。
- primary：正文与表单值。
- secondary：正文和说明。
- muted：元信息、路径摘要、disabled reason。
- disabled：占位、禁用提示和低优先级时间戳。
- on-dark 系列：深色结构区中的标题、正文和弱提示。

### 主色

```css
--ds-brand: #1F669F;
--ds-brand-hover: #185686;
--ds-brand-active: #12456D;
--ds-brand-soft: #D9E8F4;
--ds-brand-border: #82A9C9;
```

使用场景：
- 主按钮、focus ring、当前工作流阶段。
- `accent-soft` 用于轻量信息提示，不用于大面积渐变。

### 分子辅助色

```css
--ds-molecule: #287C80;
--ds-molecule-soft: #E2EEEE;
```

使用场景：
- 结构、receptor/ligand、Viewer 层开关、分子文件状态。
- 只作点缀，不作为大面积背景。

### Vina / docking 辅助色

```css
--ds-vina: #655FAF;
--ds-vina-soft: #EAE8F4;
```

使用场景：
- Vina 参数、运行配置、对接运行、pose 相关状态。
- 不用于“药效”或科学结论表达。

### 状态色

```css
--ds-success: #2F9663;
--ds-success-soft: #DBEEE3;
--ds-warning: #C9822F;
--ds-warning-soft: #F3E4CC;
--ds-danger: #B94F52;
--ds-danger-soft: #F0DCDC;
--ds-info: #3C7FAE;
--ds-info-soft: #DCEAF3;
```

使用场景：
- success：已就绪、完成。
- warning：待确认、大 box、非阻塞风险。
- danger：失败、缺失关键文件、不可继续。
- info：说明、可选步骤、下一步建议。

## 四、字体 token

```css
--ds-font-ui: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
--ds-font-mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
```

规则：
- UI 字体优先系统字体，不依赖外部 CDN。
- 路径、日志、命令、文件名使用 monospace。
- 路径和日志默认降低视觉权重，不压过主任务。

字号：

```css
--ds-text-xs: 12px;
--ds-text-sm: 13px;
--ds-text-md: 14px;
--ds-text-lg: 16px;
--ds-title-sm: 18px;
--ds-title-md: 24px;
--ds-title-lg: 32px;
```

行高：

```css
--ds-leading-tight: 1.25;
--ds-leading-normal: 1.5;
--ds-leading-relaxed: 1.7;
```

## 五、间距 token

```css
--ds-space-1: 4px;
--ds-space-2: 8px;
--ds-space-3: 12px;
--ds-space-4: 16px;
--ds-space-5: 24px;
--ds-space-6: 32px;
--ds-space-7: 48px;
```

规则：
- 页面 section 间距用 24 或 32。
- 卡片内边距用 16 或 24。
- 表单行距用 12 或 16。
- 不允许随意写 17px、23px、37px 这类魔法数字。

## 六、圆角 token

```css
--ds-radius-sm: 6px;
--ds-radius-md: 8px;
--ds-radius-lg: 10px;
--ds-radius-xl: 14px;
```

规则：
- 普通按钮和输入框用 sm/md。
- 面板用 md/lg。
- 不过度圆润，不做移动端玩具 UI。

## 七、阴影 token

```css
--ds-shadow-none: none;
--ds-shadow-soft: 0 1px 3px rgba(12, 32, 52, 0.08);
--ds-shadow-panel: 0 1px 2px rgba(12, 32, 52, 0.08),
                   0 8px 24px rgba(12, 32, 52, 0.06);
--ds-shadow-raised: 0 1px 3px rgba(12, 32, 52, 0.08);
```

规则：
- 优先用 border 和 background 区分层级。
- 阴影只用于 MainPanel、RightRail 或少数 raised panel。
- 不做厚重卡片阴影。

## 八、布局 token

```css
--ds-sidebar-width: 248px;
--ds-sidebar-collapsed-width: 76px;
--ds-topbar-height: 60px;
--ds-statusbar-height: 34px;
--ds-context-width: 316px;
--ds-content-max: 1280px;
--ds-content-wide: 1440px;
```

规则：
- 普通任务页不无限拉宽。
- Viewer 可以使用全宽 workbench。
- Dashboard 使用 max-width。
- ContextPanel 用于状态、下一步、文件、日志入口。

## 九、状态 token

| 状态 | 颜色 | icon/dot | 一句话解释 | 下一步建议 |
| --- | --- | --- | --- | --- |
| ready | success | 绿色 dot | 当前输入或工具已就绪 | 继续下一步 |
| missing | danger | 红色 dot | 必需文件或工具缺失 | 回到对应步骤补齐 |
| partial | warning | amber dot | 信息不完整但可继续检查 | 查看缺失项 |
| blocked | danger | 红色 dot | 当前步骤不能继续 | 先处理阻塞原因 |
| running | info | 蓝色 dot / spinner | 任务正在执行 | 等待完成并查看日志 |
| finished | success | 绿色 dot | 任务已完成 | 查看产物或进入下一步 |
| failed | danger | 红色 dot | 上次任务失败 | 打开错误恢复建议 |
| warning | warning | amber dot | 有风险但不一定阻塞 | 阅读提示后确认 |
| optional | muted | 灰色 dot | 可选信息或高级功能 | 需要时展开 |

## 十、动效 token

允许：
- hover transition：120-180ms。
- panel enter：轻微 fade。
- loading skeleton：用于等待工具链或项目状态。

禁止：
- 粒子、发光、旋转装饰。
- 大面积动态渐变。
- 影响阅读路径、日志、表格的动画。

## 十一、主题禁令

禁止：
- cosmic galaxy；
- glassmorphism；
- cyberpunk neon；
- excessive gradients；
- game HUD；
- marketing landing page；
- AI card stack；
- random pastel colors；
- heavy shadows；
- decorative icons without meaning。
