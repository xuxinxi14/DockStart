# DockStart v0.13.6 本地 GUI 候选包构建记录

日期：2026-08-03

## 定位

本轮围绕 AutoDock Vina 官方教程的六类流程修复功能边界并生成 Basic、Assisted 两套 Windows MSI/NSIS 候选包。Assisted 已通过开发布局和打包后真实准备/对接门禁，但按本轮要求没有执行真实安装、卸载与残留检查，因此 `publishable = false`，不得直接作为公开 Release 发布。

## 本轮修复

- 柔性受体准备返回结构化审查错误后，Rust 后台调用不再改用其他 Python 重试并覆盖原始 JSON。
- `A:315` 通过残基检查后，首次严格准备会展示 Meeko 实际拟忽略的 27 个模板异常残基；用户核对并确认同一清单后才使用 `--allow_bad_res` 重试。
- 柔性受体详细诊断区限制在卡片宽度内，长命令和 JSON 在内部换行/滚动，不再把右侧结果区推离页面。
- 单分子 MOL2 可以进入普通准备和受审查的大环准备；多分子/批量 MOL2 仍明确拒绝。
- 水合结果验收把 `num_modes` 视为最大输出数量，不再把能量窗口内少于请求数量的合法结果误报为失败。
- 新增标准 AD4 maps 的 1IEP 真实验收脚本，并把六类流程整理为逐按钮人工清单。
- 后端、npm、Cargo、Tauri 与界面版本统一为 `0.13.6`。

## 六类官方流程证据

| 流程 | 本轮证据 | 结论 |
|---|---|---|
| 1IEP 基础对接 / 评分 / 局部优化 | 固定官方输入的真实 Vina 运行 | 通过 |
| 1FPU 柔性侧链 A:315 | 严格审查、确认恢复、刚性/柔性文件绑定、真实 Vina 柔性运行 | 通过；3 个 Mode，最佳 `-10.39 kcal/mol` |
| 串行批量筛选 | 6 个独立配体任务、取消/恢复、Top N 与报告 | 通过；6/6 完成 |
| BACE_1 大环 | 官方 MOL2 导入、7 个候选、确认准备、真实运行与 SDF 拓扑检查 | 通过；20 个 Mode，最佳 `-7.804 kcal/mol` |
| 1UW6 水合 AD4 | 水合配体、W map、真实 AD4 运行、后处理与报告 | 通过；7 个 Mode，最佳 `-8.261 kcal/mol` |
| 1IEP 标准 AD4 maps | 外部 AutoGrid4 生成 maps，Vina `--maps --scoring ad4` 真实运行 | 通过；9 个 Mode，最佳 `-14.75 kcal/mol` |

真实科学回归输出保存在 `.release/evidence/v0.13.6/`。AutoGrid4 由本机外部路径提供，没有包含进安装包。

## 构建与测试

| 检查 | 结果 |
|---|---|
| Python 后端 | 1199 项通过，6 项跳过 |
| TypeScript / Vite | 通过 |
| Cargo check | 通过 |
| Rust/Tauri | 34/34 通过 |
| Assisted development-layout gate | 通过；离线 RDKit/Meeko 准备与真实 Vina 运行 |
| Assisted post-package gate | 通过；从 Tauri 打包资源布局再次完成准备与真实 Vina 运行 |
| Assisted post-install gate | 未执行；`development_only`、`publishable = false` |
| GUI 布局 | 1280 px 视口无页面级横向溢出；展开诊断仍保持 585/585 px |

GUI 对照证据位于 `output/playwright/v0.13.6-ui/`，包括修复后截图与同视口前后对照图。逐按钮人工清单位于 `docs/manual_official_vina_examples_v0_13_6.md`。

## 候选安装包

| Profile | 文件 | 大小（bytes） | SHA256 |
|---|---|---:|---|
| Basic | `DockStart_0.13.6_Basic_x64_en-US.msi` | 24,355,100 | `e6a960203e56a664bb4705bf3697846464509295ddfd4a72ae0f82dc687036a7` |
| Basic | `DockStart_0.13.6_Basic_x64-setup.exe` | 18,453,426 | `11e807fb492a473fe0a7b32d68300825ed7f38f3f7267046dad1f8bb9d53d7ca` |
| Assisted | `DockStart_0.13.6_Assisted_x64_en-US.msi` | 114,139,368 | `152a1d0337ab5f64e756cbabcbd49d0a8d6e1462ffd48a08baaf1320407dd145` |
| Assisted | `DockStart_0.13.6_Assisted_x64-setup.exe` | 73,836,615 | `203507a1afdd1241f98b51b7e8497d67433b96c540ce20c4bd6ffbf00ef691bf` |

产物目录：`.release/artifacts/0.13.6/`。校验表：`.release/artifacts/0.13.6/SHA256SUMS.txt`。

## 仍需人工确认

- 从 Assisted 安装包真实安装后，按六例清单完成 GUI 全流程；当前证据不能替代这一步。
- 水合对接仍是实验性协议；标准 AD4 maps 与水合流程需要用户自行配置 AutoGrid4。
- 公开发布前必须补做隔离目录中的真实安装、运行、卸载和残留检查，并重新生成 `publishable = true` 的产物清单。
