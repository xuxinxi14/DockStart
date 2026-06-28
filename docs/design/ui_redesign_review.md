# DockStart v0.7.8 UI Structure Review

## 审计方式

- 启动方式：`apps/desktop` Vite dev server，端口 `1421`。
- 浏览器审计：Playwright CLI，视口 `1440 x 1000`。
- 实拍页面：Dashboard 无项目、工具链、创建项目、帮助。
- 项目态页面：通过源码重排审计和 `npm run build` 覆盖。普通浏览器没有 Tauri 后端，未伪造真实项目运行结果。
- 截图处理：截图只用于本地临时验收，未提交图片。

## 已检查页面

- Dashboard 无项目：Playwright 快照通过。
- 创建项目：Playwright 快照通过。
- 工具链：Playwright 快照通过；浏览器环境会显示 Tauri invoke 不可用，属于审计环境限制。
- 帮助：Playwright 快照通过。
- 获取结构：源码结构整改并通过 TypeScript 构建。
- 准备 Vina 输入：源码结构整改并通过 TypeScript 构建。
- 设置搜索范围：源码结构整改并通过 TypeScript 构建。
- 导入 PDBQT：源码结构整改并通过 TypeScript 构建。
- Vina 参数 / 配置 / 准备运行 / 执行：源码结构整改并通过 TypeScript 构建。
- Viewer Workbench：三栏工作台结构保留并压缩顶部说明，通过 TypeScript 构建。
- 结果页 / 报告页：源码结构整改并通过 TypeScript 构建。

## 主要视觉变化

- Sidebar 只保留导航，不再在底部重复项目进度。
- Topbar 收敛为项目、当前阶段、工具链简况和版本号。
- Dashboard 改为项目驾驶舱：首屏只保留创建入口、流程、工具链简况；项目态保留 5 步时间线和 6 个关键产物。
- 页面统一使用 `page-hero`、`task-layout`、`status-strip`、`next-step-strip`、`task-card` 等骨架类。
- 技术详情统一收进 `AdvancedDetails`，默认折叠。
- 运行流程拆成当前阶段展开：生成配置、准备运行、执行、结果、报告。
- Viewer 保持左 Inspector / 中央 Canvas / 右 Properties，删除顶部重复说明，让画布成为视觉中心。

## AI 味减少项

- 删除大段重复解释，页面副标题压缩为一句。
- 减少同权重卡片墙，改为主任务 + 状态摘要 + 下一步。
- raw / PDBQT / Box / run / scores / report 的关系更直接。
- stdout、stderr、metadata、命令预览、绝对路径和记录一致性默认折叠。
- best affinity 只显示为“对接评分”，不做药效或真实结合判断。

## 仍需后续优化

- “打开已有项目”仍是禁用占位，需要后续接真实 Tauri 打开项目入口。
- 本轮未做真实桌面端文件选择点击审计；项目态页面依赖构建验证和源码审计。
- 帮助页的流程卡仍可进一步压缩，但已低于旧版说明密度。
- 图标体系仍较弱，本轮优先解决布局骨架、层级和文案噪声。

## 功能入口保留

- 保留：结构获取、PDBQT 导入、RDKit/Meeko 准备、Box、Vina 参数、配置生成、运行记录、执行、结果解析、报告导出、Viewer、工具链、设置、帮助。
- 未新增：PLIP、ProLIF、Open Babel、MGLTools、相互作用分析、pocket prediction、药效判断或 Vina 算法改动。

## 符合性结论

- 符合 Molecular Workbench 方向：专业、冷静、低噪声、流程驱动、状态明确。
- 避免了 OrbitStart/galaxy 风格、赛博霓虹、玻璃拟态、营销页和后台模板堆卡片感。
