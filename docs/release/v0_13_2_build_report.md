# DockStart v0.13.2 本地 GUI 候选包构建记录

日期：2026-08-02

## 定位

本轮按人工 GUI 复测需求生成 Basic 与 Assisted 的 MSI/NSIS 本地候选包。它们未执行完整发布门禁，`publishable = false`，不得作为公开 Release 发布。

## 本轮修复

- 调整项目创建页“本次任务”标题层级与顶部间距，移除重复的输入来源说明。
- 在 PDBQT 与 SDF/MOL 路径中显示全部已选配体，并提供逐项更改、删除操作。
- 修正运行工作台与水合 AD4 的侧栏完成状态聚合，结构准备失败时不再误报成功。
- 为原子记录被拆散、且可由唯一原子序号无损恢复顺序的 PDB 提供审计中间文件路径；官方 AutoDock Vina `1iep_receptorH.pdb` 已用随附 Meeko 0.7.1 实测转换成功。
- 收紧并居中结构准备页“查看检测详情”折叠控件。

## 已执行的针对性验证

| 检查 | 结果 |
|---|---|
| 版本一致性 | 后端、npm、Cargo、Tauri、界面共七处均为 `0.13.2` |
| 受体准备定向测试 | 28/28 通过 |
| 侧栏状态定向测试 | 5/5 通过 |
| TypeScript / Vite 生产构建 | 通过 |
| 官方 1IEP 受体真实准备 | 随附 Python + Meeko 0.7.1 成功生成 218,862 字节 PDBQT；4412 条 ATOM/HETATM 原始记录字节集合保持不变 |
| Playwright GUI 冒烟 | 1728 × 1040 暗色主题创建页通过；无水平溢出或任务卡裁切 |
| Basic 白名单资源 stage | 通过；Vina 1.2.7、Python 3.11.15 |
| Assisted 离线资源 stage | 通过；Meeko 0.7.1、RDKit 2026.3.3，未使用网络 |
| Basic / Assisted Tauri MSI + NSIS 构建 | 通过 |

未按用户要求执行：完整后端测试、`cargo test`、Basic/Assisted post-package 科学回归、Assisted 真实安装/卸载门禁。因此本记录不能替代正式发布报告。

## 候选安装包

| Profile | 文件 | 大小（bytes） | SHA256 |
|---|---|---:|---|
| Basic | `DockStart_0.13.2_Basic_x64_en-US.msi` | 24,334,620 | `6868ee4707e1fd67d365c65bad81f8e2187f368c4f85b4f388be81edeff75b33` |
| Basic | `DockStart_0.13.2_Basic_x64-setup.exe` | 18,440,654 | `724d3ac79872dcd4fb97d1217605f246bcb62f0d92870f2bb40483d10734d6f4` |
| Assisted | `DockStart_0.13.2_Assisted_x64_en-US.msi` | 114,135,272 | `0159176ded7977767b43ae93680b0346e3df4d4c129834c0bcad6585d4d83ec3` |
| Assisted | `DockStart_0.13.2_Assisted_x64-setup.exe` | 73,820,293 | `bf619a4c75194b03d92415329a38cc1951badcd1ae0e5656920d31a1e14d2c75` |

产物目录：`.release/artifacts/0.13.2/`。校验表：`.release/artifacts/0.13.2/SHA256SUMS.txt`。候选清单明确记录 `publishable = false`。

## GUI 证据

- 创建页截图：`output/playwright/v0.13.2-ui/project-create-task-spacing.png`
- 修改前后组合图：`output/playwright/v0.13.2-ui/comparison-task-spacing.png`

