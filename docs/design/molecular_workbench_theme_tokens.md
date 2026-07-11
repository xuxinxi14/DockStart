# DockStart Molecular Workbench Theme Tokens

## 一、主题命名

主题名称：**DockStart Instrument Console**

中文名：**深蓝分子工作台**

一句话定义：

DockStart 的视觉风格应像一台现代分子建模与对接实验仪器：深蓝应用壳负责建立稳定边界，浅色任务画布负责承载参数、文件和表格。整体专业、冷静、可信、低噪声，以流程引导和状态判断为核心，而不是炫酷装饰。

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
--ds-bg-app: #EAF0F6;
--ds-bg-subtle: #F0F4F8;
--ds-bg-panel: #FFFFFF;
--ds-bg-panel-soft: #F7FAFC;
--ds-bg-elevated: #FFFFFF;

--ds-navy-950: #061A2F;
--ds-navy-900: #08233F;
--ds-navy-850: #0B2D50;
--ds-navy-800: #10395F;
--ds-navy-700: #164A78;
--ds-shell-text: #F6F9FC;
--ds-shell-text-muted: #AAC0D6;
```

使用场景：
- `--ds-bg-app`：应用底色和主 canvas 外层。
- `--ds-bg-subtle`：次级区域、技术详情背景。
- `--ds-bg-panel`：主面板、表单、表格。
- `--ds-bg-panel-soft`：弱提示、空状态、折叠详情。
- `--ds-bg-elevated`：浅色浮层和 Viewer 内部属性卡。
- `--ds-navy-*`：Sidebar、Topbar、ContextPanel、Viewer 工作轨和底部状态栏；深蓝覆盖约 35%–40%，但主表单与数据表保持浅色。

### 边框

```css
--ds-border-subtle: #D8E1EA;
--ds-border-default: #C7D3DF;
--ds-border-strong: #9FB0C1;
```

使用场景：
- subtle：普通分隔线。
- default：面板、输入框、表格边界。
- strong：active state、关键分隔、viewer workbench 边界。

### 文字

```css
--ds-text-primary: #0B1D31;
--ds-text-secondary: #3E5268;
--ds-text-muted: #5C7187;
--ds-text-faint: #7F91A3;
--ds-text-inverse: #FFFFFF;
```

使用场景：
- primary：标题、关键数据。
- secondary：正文和说明。
- muted：元信息、路径摘要、disabled reason。
- faint：占位、辅助标签、低优先级时间戳。
- inverse：深色按钮内文字。

### 主色

```css
--ds-accent: #1F6FCB;
--ds-accent-hover: #175BAB;
--ds-accent-soft: #E1EEFB;
--ds-accent-border: #A9CAE9;
```

使用场景：
- 主按钮、focus ring、当前工作流阶段。
- `accent-soft` 用于轻量信息提示，不用于大面积渐变。

### 分子辅助色

```css
--ds-molecule: #167F86;
--ds-molecule-soft: #E1F2F3;
```

使用场景：
- 结构、receptor/ligand、Viewer 层开关、分子文件状态。
- 只作点缀，不作为大面积背景。

### Vina / docking 辅助色

```css
--ds-vina: #6C63FF;
--ds-vina-soft: #EEECFF;
```

使用场景：
- Vina 参数、运行配置、对接运行、pose 相关状态。
- 不用于“药效”或科学结论表达。

### 状态色

```css
--ds-success: #177A46;
--ds-success-soft: #E7F6EE;
--ds-warning: #9B5A12;
--ds-warning-soft: #FFF4DC;
--ds-danger: #B83232;
--ds-danger-soft: #FDECEC;
--ds-info: #1E63A7;
--ds-info-soft: #EAF3FF;
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
--ds-shadow-soft: 0 1px 2px rgba(6, 26, 47, 0.07);
--ds-shadow-panel: 0 14px 32px rgba(6, 26, 47, 0.10);
```

规则：
- 优先用 border 和 background 区分层级。
- 阴影只用于 Topbar、弹层或关键 elevated panel。
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
