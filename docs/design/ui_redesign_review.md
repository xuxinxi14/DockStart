# DockStart v0.7.6 UI Redesign Review

## 审计方式

- 启动方式：`apps/desktop` Vite dev server，端口 `5177`。
- 浏览器审计：Playwright CLI，视口 `1440 x 1000`。
- 项目态页面：普通浏览器没有 Tauri WebView 后端，因此注入临时 `window.__TAURI_INTERNALS__.invoke` mock，只用于截图审计；未改动源码和后端能力。
- 临时截图目录：`output/playwright/dockstart-v0.7.6/`。截图不提交，审计结束后清理临时目录，仓库只保留本 Markdown 报告。

## 已检查页面

- Dashboard 无项目：`dashboard-no-project.png`
- Dashboard 有项目 mock：`dashboard-project.png`
- 创建项目：`project-create.png`
- 工具链：`toolchain.png`
- 获取结构：`structure-fetch.png`
- 准备 PDBQT / Vina 输入：`preparation.png`
- Viewer Workbench：`viewer.png`
- Vina 配置：`vina-config.png`
- Vina 运行准备：`run-prepare.png`
- Vina 执行：`run-execute.png`
- 结果：`result.png`
- 报告：`report.png`
- 帮助：`help.png`

## 主要视觉变化

- 建立 Molecular Workbench 主题：浅冷灰蓝背景、白色面板、深实验蓝主色、低噪声状态色。
- 应用外壳改为 Project / Workflow / Workbench / Support 分组，导航表达任务流程，不再像 React 页面列表。
- 顶栏改为项目、阶段、工具链简况和版本号，避免“当前页面 / 当前项目”式调试文案。
- Dashboard 改成项目驾驶舱：首屏直接给出下一步建议、流程时间线、产物状态和科学风险提示。
- 工具链页改成配置向导，弱化 sha256、manifest、resource_dir 等技术字段。
- Viewer 改成工作台布局：左侧 Inspector、中间 Canvas、右侧文件/构象属性，Box 参数贴近 Viewer。
- Vina 运行页用 workflow bar 串起配置、准备、执行、解析、报告，减少孤立卡片感。

## AI 味减少项

- 降低同权重卡片堆叠，改为“主任务 + 状态 + 下一步”的页面结构。
- 技术详情默认折叠或放在次级区域，主 UI 不展示组件名、metadata dump 或 raw_error。
- raw / prepared / config / run / report 在主界面分别表达为原始结构文件、Vina 输入文件、运行配置、对接运行、实验记录。
- best affinity 统一表达为“对接评分”，不暗示药效或真实结合。
- 页面 spacing、边框、按钮、状态 pill 和面板层级统一走 design tokens。

## 仍需后续优化

- “打开已有项目”仍是禁用占位，后续需要真实 Tauri 后端入口。
- 项目态截图依赖审计 mock，不能替代真实桌面端文件选择、真实 Tauri invoke 和真实结构文件渲染 QA。
- 少量产物路径如 `prepared/receptor.pdbqt`、`configs/vina_config.txt`、`scores.csv` 会保留在文件状态区域，这是可追踪性需求。
- 图标体系仍较弱，本轮优先完成信息架构、token、页面层级和工作流引导。
- Viewer 的 3Dmol 审计使用极小 mock PDB 片段，只证明 canvas 管线可显示，不能代表复杂结构渲染质量。

## 功能入口保留

- 保留：PDBQT 导入、RCSB/PubChem 原始结构获取、RDKit/Meeko 准备、Box 参数、Vina 参数、配置生成、运行准备、Vina 执行、结果解析、报告导出、3D 查看、工具链检测和设置入口。
- 未新增：PLIP、ProLIF、Open Babel、MGLTools、相互作用分析、pocket prediction、药效判断或 Vina 算法改动。

## Molecular Workbench 符合性

- 符合：专业、克制、低噪声、工作流驱动、状态明确、科学边界清楚。
- 避免：cosmic galaxy、glassmorphism、cyberpunk neon、game HUD、marketing landing page、AI card stack。
