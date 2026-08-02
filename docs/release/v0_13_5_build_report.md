# DockStart v0.13.5 本地 GUI 候选包构建记录

日期：2026-08-03

## 定位

本轮按人工 GUI 复测要求生成 Basic 与 Assisted 的 MSI/NSIS 本地候选包。执行了 1FPU 受体转换的针对性科学回归与界面检查，没有执行完整发布门禁；`publishable = false`，不得直接作为公开 Release 发布。

## 本轮调整

- 帮助页快速开始区改为两个等宽卡片，填满可用区域。
- “故障排查”改为“常见问题”，压缩标题与折叠项说明。
- 最小化、最大化/还原和关闭按钮扩展为 56 px，并移除浏览器原生 `title` 提示。
- Meeko 严格准备发现不完整残基时，返回完整残基清单；只有用户确认同一清单后才允许使用 `--allow_bad_res` 重试。
- 重试输出发布前再次比较用户确认清单与 Meeko 实际忽略清单；不一致时拒绝发布候选 PDBQT。
- 同步后端、npm、Cargo、Tauri 与界面版本为 `0.13.5`。

## 1FPU 官方示例回归

输入来自 AutoDock Vina 官方仓库：`example/flexible_docking/data/1fpu_receptorH.pdb`。

| 检查 | 结果 |
|---|---|
| 严格模式 | 按预期失败；准确返回 27 个模板不匹配残基 |
| 用户确认恢复 | 使用完全相同的 27 项清单启用 `--allow_bad_res` |
| 实际忽略清单核对 | 与确认清单一致 |
| 输出 | 成功生成 `prepared/receptor.pdbqt`，196,587 bytes |
| 审计记录 | `preparation/receptor_002/metadata.json` 记录清单、SHA256 与一致性结果 |

针对性单元测试 3/3 通过，覆盖严格失败、受审查恢复和既有逐残基控制合同。

## GUI 快速验证

| 检查 | 结果 |
|---|---|
| TypeScript / Vite 生产构建 | 通过；打包时再次通过两次 |
| 帮助页两列布局 | 527 px + 527 px，容器 1064 px |
| 窗口按钮 | 三个按钮均为 56 × 59 px；无 `title` 属性 |
| 页面宽度 | `clientWidth = scrollWidth = 1728` |
| Basic 资源 | Vina 1.2.7、Python 3.11.15；不含 RDKit/Meeko |
| Assisted 资源 | Vina 1.2.7、Python 3.11.15、Meeko 0.7.1、RDKit 2026.3.3；离线准备 |
| Basic / Assisted MSI + NSIS | 通过 |

GUI 证据位于 `output/playwright/v0.13.5-ui/`，包括帮助页、常见问题与三组源图对照。

未执行：完整后端测试、`cargo test`、科学功能全量回归、真实安装/卸载和 post-install 门禁。因此本记录不能替代正式发布报告。

## 候选安装包

| Profile | 文件 | 大小（bytes） | SHA256 |
|---|---|---:|---|
| Basic | `DockStart_0.13.5_Basic_x64_en-US.msi` | 24,338,716 | `4ccd9a0926daf1518b57ddadd225e5bece19118c801f76a7b313b3b118f6349c` |
| Basic | `DockStart_0.13.5_Basic_x64-setup.exe` | 18,454,693 | `fd2fb89bef9cab364eed460647ef8b2a62efb04e9e7a527b8035e1327c0372c1` |
| Assisted | `DockStart_0.13.5_Assisted_x64_en-US.msi` | 114,147,560 | `1e2c6d2bb76451c09b4b2936a66ff41bc919d21329a7c9b9c3eeb0c13bb94369` |
| Assisted | `DockStart_0.13.5_Assisted_x64-setup.exe` | 73,841,113 | `1e419b4c16b6fa1a9e141e5bdce9fa305802dbb2dee671f114c093754cc1f8d5` |

产物目录：`.release/artifacts/0.13.5/`。校验表：`.release/artifacts/0.13.5/SHA256SUMS.txt`。
