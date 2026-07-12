# DockStart 3D 工作台与运行流程设计 QA

## 审查范围

- 目标：重排 3D 工作台、加入 XYZ 方向轴和 Box 单参数滚轮绑定、统一折叠导航与信息框、消除页面切换和按钮点击时的主线程卡顿。
- 参考图：用户提供的 5 张 1588 × 1014 / 局部界面截图。
- 实现环境：真实 Tauri WebView2 桌面窗口，不是静态网页替身。
- 核心视口：1588 × 1014；补充检查 1200 × 800 响应式布局。
- QA 项目：`.codex-ui-audit/viewer-workbench-redesign/qa-final/basic_demo_001`。

## 对照证据

- 改造前 Viewer：`.codex-ui-audit/viewer-workbench-redesign/baseline/01-current-viewer.png`
- 改造前折叠导航：`.codex-ui-audit/viewer-workbench-redesign/baseline/02-collapsed-sidebar.png`
- 最终 Viewer 首屏：`.codex-ui-audit/viewer-workbench-redesign/qa-final/01-viewer-top.png`
- Viewer 尺寸 X 滚轮绑定：`.codex-ui-audit/viewer-workbench-redesign/qa-final/02-viewer-box-bound.png`
- 下方仪表盘滚动修复：`.codex-ui-audit/viewer-workbench-redesign/qa-final/04-viewer-dashboard-fixed.png`
- 最终折叠导航：`.codex-ui-audit/viewer-workbench-redesign/qa-final/05-collapsed-sidebar.png`
- 蓝色下一步区域：`.codex-ui-audit/viewer-workbench-redesign/qa-final/06-callouts.png`
- 绿色结果区域：`.codex-ui-audit/viewer-workbench-redesign/qa-final/07-green-result.png`
- 运行页 Box 绑定与 XYZ 轴：`.codex-ui-audit/viewer-workbench-redesign/qa-final/08-run-box-bound.png`
- 真实运行完成状态：`.codex-ui-audit/viewer-workbench-redesign/qa-final/09-run-complete.png`
- 参考与实现同屏比较：`.codex-ui-audit/viewer-workbench-redesign/qa-final/10-viewer-comparison.png`

## 可见设计核对

| 目标 | 结果 | 证据 |
| --- | --- | --- |
| 取消左右夹持画布 | 通过 | 画布占主列，右侧只保留结构加载、图层、Pose、Box 等互动信息。 |
| 仪表盘移至画布下方 | 通过 | 当前文件、Box 摘要、可查看文件按三列对齐；窄屏自动改为两列/单列。 |
| 滚动时不遮挡仪表盘 | 通过 | 真实滚动发现 sticky 画布覆盖问题后已移除错误粘性定位，复测无重叠。 |
| XYZ 方向提示 | 通过 | Viewer 与运行页均显示随模型旋转的 X 红、Y 绿、Z 蓝真实 3Dmol 轴。 |
| Box 控制清晰 | 通过 | 六项参数各有独立绑定按钮、单选高亮、步进选择和实时状态说明。 |
| 折叠导航统一 | 通过 | 11 个入口均为 48 × 48 点击区、20 × 20 图标；组内 5px、组间 18px。 |
| 蓝/绿信息框排版 | 通过 | 统一 4px 语义边线、12px/14px 内边距、标题/正文行高；按钮宽屏右对齐，窄屏整行换行。 |
| 深蓝视觉区域 | 通过 | 导航、顶部栏、3D 画布、右侧工作栏、运行前检和底部状态栏组成连续深蓝框架。 |

## 核心交互核对

- Viewer：绑定“尺寸 X”后，滚轮将 Box 从 `12` 调整到 `12.1`，其余五项不变；取消绑定后再次滚动，六项 Box 值均不再改变，恢复 3Dmol 默认缩放。
- Viewer：六个绑定按钮同一时间只有一个 `aria-pressed=true`；中心参数允许负值，尺寸参数最小为 0.1 Å。
- 运行页：默认使用 0.1 Å 细调；快速 5 Å 步进向下滚动时，尺寸由 0.2 Å 正确钳制到 0.1 Å，不会反向跳到 5 Å。
- 运行页：尺寸 X 绑定、取消绑定、XYZ 轴显示均在真实桌面窗口验证。
- Result：完整流程生成报告后，按钮改为“查看实验记录”，右栏显示 report 路径，不再错误提示“待导出”。
- Pose：`run_002` 的 9 个构象可读取，mode 1 自动加载到 Viewer，当前评分和图层状态同步显示。

## 性能核对

- 原根因：每个 Tauri `invoke` 同步启动 Python；`workflow-status` 本机 5 次中位耗时约 248.6 ms，Home 在 StrictMode 下可重复触发 5–6 次。
- 修复：62/62 Tauri 命令全部异步化，Python 执行进入 blocking 线程池；短 TTL 只读缓存合并同键并发请求，写操作按 generation 失效。
- 创建示例并导航：256.2 ms 完成，主线程最大间隙 17.7 ms。
- Viewer 加载受体 + 配体：251.6 ms 完成，主线程最大间隙 7.6 ms。
- Viewer 首次懒加载：577.3 ms，主线程最大间隙 40.6 ms；后续运行页切换 55.9 ms。
- 完整真实 docking：约 3.9 s 完成，期间主线程最大间隙 37.3 ms；按钮和页面未被 Python 阻塞。

## 真实对接流程

- 第一次 UI smoke 暴露真实竞态：Vina 已生成 `out.pdbqt`，但 500 ms 状态轮询在执行器写入终态前把 run 错写为 `interrupted`。
- 最终修复不再依赖固定时间猜测：metadata 记录 Vina 子进程与 DockStart 执行器双重创建身份；收尾阶段使用二次探测、事务内 CAS 和身份重验。
- 取消操作若发生在子进程退出与执行器收尾之间，不终止未知 PID，也不覆盖成功/失败终态。
- 最终真实 `run_003`：`finished`，exit code 0，最佳评分 -0.6916 kcal/mol，生成 9 个 pose、`out.pdbqt`、`scores.csv`、run 报告和项目报告。
- 结果页自动读取 9 行评分；实验记录和 mode 1 Viewer 路径均可继续操作。

## 科学与产品边界

- 保留“Docking score 仅供结构结合趋势参考，不能替代实验验证”。
- Box 几何工具只按已加载坐标定位，不宣称自动识别结合口袋。
- 未新增分子动力学、AI 药效预测、大规模虚拟筛选或新的科学依赖。
- 未复制第三方商业软件源码或专有视觉资产。

## 自动化验证

- `python -m unittest discover -s backend/tests`：289/289 通过。
- `npm run build`：通过。
- `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`：通过。
- Rust cache 测试：2/2 通过。
- `git diff --check`：通过，仅有仓库现有 LF/CRLF 提示。
- 构建仍报告 3Dmol 第三方 `eval` 与 587 kB 懒加载分包警告；未影响本轮功能和桌面 smoke，列为 P3 构建优化项。

## 结论

同屏参考核对、真实桌面交互、完整对接和并发收尾回归均通过。未发现仍阻塞本轮 3D 工作台、Box 操作、侧栏、信息框或主流程性能的 P0/P1/P2 问题。

final result: passed
