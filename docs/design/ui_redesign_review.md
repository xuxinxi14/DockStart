# DockStart V0.7 UI Redesign 截图审计

## 审计方式

- 启动方式：`apps/desktop` Vite dev server，端口 `5177`。
- 浏览器审计：Playwright CLI。
- 项目态页面：普通浏览器没有 Tauri WebView 后端，因此注入临时 mock `window.__TAURI_INTERNALS__.invoke` 只用于截图审计；未改动源码和后端能力。
- 截图位置：`output/playwright/dockstart-v0.7/`，不提交截图文件。

## 已检查页面

- Dashboard 无项目：`dashboard-no-project.png`
- Dashboard 有项目 mock：`dashboard-project.png`
- Toolchain：`toolchain.png`
- ProjectCreate：`project-create.png`
- StructureFetch：`structure-fetch.png`
- Preparation：`preparation.png`
- Viewer Workbench：`viewer.png`
- Vina config：`vina-config.png`
- Run prepare：`run-prepare.png`
- Run execute：`run-execute.png`
- Result：`result.png`
- Report：`report.png`
- Help：`help.png`

## 已修复的 AI 味问题

- 侧边栏从 React page 列表改成 Project / Workflow / Workbench / Support 分组。
- 顶栏去掉“当前页面 / 当前项目”式调试标题，改成工作流阶段、项目、工具链简态和版本。
- 无项目 Dashboard 改成明确首屏：创建项目、打开已有项目占位、查看新手流程。
- 有项目 Dashboard 改成项目驾驶舱：下一步建议、Workflow Timeline、产物状态、科学风险提示。
- 工具链页从状态 dump 改成三组配置向导：AutoDock Vina、Python + RDKit + Meeko、内置资源。
- Viewer 改成三栏 Workbench：Inspector / Canvas / Properties，3D canvas 成为视觉中心。
- 技术字段如 sha256、manifest、resource_dir、raw_error 默认进入技术详情。
- 主 UI 清理组件名式标题，不再显示 ProjectCreatePage、ToolchainStatusPage 等。
- raw / prepared / config / run / report 在主 UI 中改为原始结构文件、Vina 输入文件、运行配置、对接运行、实验记录。
- best_affinity 改为“最佳对接评分”，不写成药效或疗效判断。

## 仍需后续优化

- 目前“打开已有项目”没有真实后端入口，只在无项目 Dashboard 保留禁用占位。
- 项目态截图使用 mock Tauri 后端，不能替代真实桌面端文件选择、Tauri invoke 和 3Dmol 真实结构渲染 QA。
- 仍有少量文件名/路径类技术文本会出现 `configs/vina_config.txt`、`prepared/receptor.pdbqt`、`scores.csv`，这些保留在产物说明中。
- 未新增图标体系；本轮优先做 IA、spacing、token 和页面层级。

## 功能入口保留

- 保留 PDBQT 导入、RCSB/PubChem 原始结构获取、RDKit/Meeko 准备、Box 参数、Vina 参数、配置生成、运行准备、Vina 执行、结果解析、报告导出、3D 查看、工具链检测和设置入口。
- 未新增 PLIP、ProLIF、Open Babel、MGLTools、相互作用分析、pocket prediction、药效判断或 Vina 算法改动。
