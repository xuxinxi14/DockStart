# DockStart v0.14.0 UI 升级与构建报告

生成日期：2026-08-04（Asia/Shanghai）

## 结论

本轮升级完成了工作台信息架构、视觉层级、交互反馈、键盘可访问性和响应式布局的统一优化，
未扩展 AutoDock Vina 算法或 MVP 科学能力边界。Basic Stable 已完成正式打包门禁；Assisted
已完成 development 与 post-package 门禁并生成开发安装包。由于构建机已有 DockStart 注册安装，
Assisted 的真实 post-install 门禁被安全跳过，因此其 manifest 明确记录
`post_install_gate=pending`、`publishable=false`，不可作为正式发布产物。

## 范围与影响判断

- 阶段归属：界面产品化属于现有 MVP/第二阶段能力的呈现优化；未新增科学工作流。
- 外部依赖：无新增 npm、Cargo 或 Python 依赖。
- 许可证：无新增第三方集成，不需要修改许可证策略。
- 文件结构：删除三个未使用组件与两个已被统一样式取代的空/旧样式入口；新增 Tooltip、延迟加载与发布安全 helper。
- 用户项目数据：不迁移、不重写项目数据；发布构建已与当前注册安装目录隔离。

## 主要升级

### 工作流与信息架构

- 项目首页统一为四个主阶段，结构获取与转换保留可见子步骤，并只保留一个动态主行动入口。
- 运行工作台把三维结构预览置于 AutoGrid maps 之前，优先支持箱体与结构核对。
- 结构准备页默认只展示关键结构事实和警告，完整审计信息收纳到高级详情。
- 结果页补齐评分、日志与分析报告标签页的方向键、Home、End 键盘导航。
- 报告页将操作条与实际 Markdown 只读预览连续呈现，减少重复卡片和上下文跳跃。

### 设计系统与交互

- 新增统一 Tooltip，支持延迟指针打开、即时键盘焦点打开、视口边界约束和 Escape 关闭。
- Tooltip 使用主题无关的深色高对比语义 token，解决浅色主题下白字低对比问题。
- 新增加载延迟与全局操作对话框的引用计数、焦点恢复、焦点陷阱、inert 和滚动锁定。
- 统一按钮、状态、空状态、错误恢复、工具链状态与窗口控件的视觉和语义。
- 恢复文本选择；移除全局右键菜单拦截；补充页面标题、焦点、进入动画和 live announcement。
- 增加 reduced-motion 适配；响应式布局覆盖 1600 px 与 960 px 视口。

### 后端一致性修复

- 手动导入 PDBQT 时保留仍在运行的 preparation ownership，确保输出竞争记录为
  `PREPARATION_OUTPUT_CONFLICT`，而不是丢失审计指针。
- raw 引用与 prepared 字节同时变化时优先报告输出冲突，避免错误归类为输入过期。
- screening 归档比较新增 `scoring` 与 `scoring_protocol` 的双向配对校验，拒绝被篡改的不一致 AD4/Vina 归档。

## 发布构建安全修复

构建前检查发现当前注册安装目录为：

```text
E:\DockStart\apps\desktop\src-tauri\target\release\DockStart
```

旧脚本会在 post-install 防护之前清理该目录。本轮将正式发布的 Cargo/Tauri 输出隔离到：

```text
.release/cargo-target/basic/
.release/cargo-target/assisted/
```

并新增只读、fail-closed 的发布安全 preflight：

- 枚举 HKCU/HKLM 的 32/64 位 DockStart 卸载记录和 publisher 默认路径；
- 规范化 InstallLocation、UninstallString、环境变量、引号和 junction/symlink；
- 要求所有清理根严格位于仓库 `.release/` 下；
- 对安装路径与清理根执行双向祖先/重叠检查；
- 默认 Assisted 正式流程检测到任何已有安装时，在生成 stage 前即拒绝继续；
- 用 `cargo metadata` 断言 Tauri 实际继承了 profile 专用的绝对 `CARGO_TARGET_DIR`。

构建前后对现有安装的三个文件进行 SHA256 比对，结果完全一致：

| 文件 | SHA256 |
| --- | --- |
| `dockstart-desktop.exe` | `0534c40aadab744dffd6ac081cddcea6c7798da3449b7f223db66805c1826f20` |
| `uninstall.exe` | `c49c006cb17f9d9b0126a30e4dc4e3b806d52f1a318ca6ddeccb248d2bf696f1` |
| `resources/toolchain_manifest.json` | `7e62474f97087de9431bf713a594d850c19d32020af5752de7407f9e451307ba` |

## 验证结果

| 门禁 | 结果 |
| --- | --- |
| 前端异步测试 | 93/93 通过 |
| TypeScript + Vite 生产构建 | 通过；4678 modules transformed |
| 后端全量测试 | 1237 tests；1231 通过、6 跳过、0 失败/错误；Basic 与 Assisted 构建各执行一次 |
| Rust 测试 | 35/35 通过 |
| PowerShell 发布脚本语法 | Basic/Assisted 均 0 个解析错误 |
| 发布安全专项测试 | 18/18 通过（16 个 helper 测试 + 2 个脚本集成约束测试） |
| `git diff --check` | 通过；仅存在仓库既有 LF/CRLF 转换警告 |
| Basic post-package | 通过；真实 Vina 运行与重复运行均 finished，9 个 pose |
| Assisted development | 通过；离线 CIF/SDF 准备、Vina、结果与报告完整闭环 |
| Assisted post-package | 通过；从 MSI 提取布局重复完整准备与对接闭环 |
| Assisted post-install | 未运行；已有安装保护生效，manifest 为 pending / publishable=false |

Playwright 视觉验收覆盖帮助页、项目创建、四阶段首页、运行 3D 优先布局、结果标签、报告连续预览、
960 px 响应式、暗色/浅色主题和 Tooltip。检查页面无横向溢出，浏览器控制台为 0 warning/error；
方向键/Home/End 标签导航、Escape 关闭 Tooltip 和 reduced-motion 均已实测。
截图位于未纳入版本控制的 `output/playwright/v0.14-ui-upgrade/`。

## 构建产物

| Profile | 文件 | 大小（bytes） | SHA256 | 状态 |
| --- | --- | ---: | --- | --- |
| Basic | `DockStart_0.14.0_Basic_x64_en-US.msi` | 24,404,252 | `08a48d7c6e512e4994583a5d4e0f3178f3170e8cd0299187d6b6b188e766f346` | 门禁通过 |
| Basic | `DockStart_0.14.0_Basic_x64-setup.exe` | 18,481,664 | `77eb9e462ee5daaed00f1ac84cd7598593c0165aac12eb83c400ed7ef3b1474e` | 门禁通过 |
| Assisted | `DockStart_0.14.0_Assisted_x64_en-US.msi` | 114,188,520 | `275d6d5805a910a6d9775f20541434ead165ea69a2241a71c1603fcdee89e2f7` | development-only |
| Assisted | `DockStart_0.14.0_Assisted_x64-setup.exe` | 73,861,266 | `30361dd8f40903c5df61d68afb9064a342ab71c9e8e2876236be355ec2e69dbe` | development-only |

归档位置：

```text
.release/artifacts/0.14.0/basic/
.release/artifacts/0.14.0/assisted/
```

## 已知风险与后续事项

- Vite 仍报告 3Dmol 上游 `eval` 警告，以及 3Dmol/主入口大于 500 kB 的分块警告；构建成功，后续可单独评估更细代码分割。
- Assisted 若要成为可发布产物，必须在没有任何 DockStart 安装的干净 Windows 或一次性账户中运行默认正式入口，完成真实 NSIS 安装、回归、卸载与残留检查；不得把本报告中的开发产物标记为 Stable。
- Basic 当前正式脚本执行 MSI 管理提取与 post-package Vina 回归，但不执行现有的 NSIS 真实安装/卸载 gate；正式对外发布前建议在干净系统补做独立安装烟雾测试。
- 发布脚本中的 branch 与 clean-worktree 检查目前仍被历史代码临时注释；发布人员需人工核对分支与工作树，或后续恢复强制门禁。
- 两个 profile 仍共享 `apps/desktop/dist/`，必须串行构建，不能并行。

科学边界不变：Docking score 仅供结构结合趋势参考，不能替代实验验证。
