# DockStart v0.13.3 本地 GUI 候选包构建记录

日期：2026-08-02

## 定位

本轮按人工 GUI 复测需求生成 Basic 与 Assisted 的 MSI/NSIS 本地候选包。它们未执行完整发布门禁，`publishable = false`，不得作为公开 Release 发布。

## 本轮调整

- 将 Vina/Vinardo 预计算 maps 的运行限制、说明和“保存当前网格”操作合并为一个紧凑模块，并与“导入 maps”模块并排对齐。
- 放大结构获取页受体与配体摘要栏中的英文步骤标签和中文角色标题。
- 同步后端、npm、Cargo、Tauri 与界面版本为 `0.13.3`。

## 已执行的快速验证

| 检查 | 结果 |
|---|---|
| 版本一致性 | 后端、npm、Cargo、Tauri、界面共七处均为 `0.13.3` |
| TypeScript / Vite 生产构建 | 通过 |
| Playwright maps 定向检查 | 两个 action card 同高；无水平或垂直溢出 |
| Playwright 结构摘要定向检查 | 受体/配体标题单行显示；无水平溢出 |
| Basic 资源 stage | 通过；Vina 1.2.7、Python 3.11.15 |
| Assisted 离线资源 stage | 通过；Meeko 0.7.1、RDKit 2026.3.3，未使用网络 |
| Basic / Assisted Tauri MSI + NSIS 构建 | 通过 |

未按用户要求执行：完整后端测试、`cargo test`、post-package 科学回归、真实安装/卸载门禁。因此本记录不能替代正式发布报告。

## 候选安装包

| Profile | 文件 | 大小（bytes） | SHA256 |
|---|---|---:|---|
| Basic | `DockStart_0.13.3_Basic_x64_en-US.msi` | 24,342,812 | `7016ecef7bccf5c0cb730b2292e1d57d641a1bc818bc2ba7c33bbde82c1a2277` |
| Basic | `DockStart_0.13.3_Basic_x64-setup.exe` | 18,454,610 | `257e174c27384047633af0a7a49a7e8cbed8a1b1697df82627cc95c8e6fcdddc` |
| Assisted | `DockStart_0.13.3_Assisted_x64_en-US.msi` | 114,122,984 | `aa1c1e207c88df397b847fa5635dc907a8972644cacf9d9bef795d341742c3a9` |
| Assisted | `DockStart_0.13.3_Assisted_x64-setup.exe` | 73,826,427 | `de94499dc2c6dcdee1106c8a6def2fe431be7a16c6cf513269c03525341f53be` |

产物目录：`.release/artifacts/0.13.3/`。校验表：`.release/artifacts/0.13.3/SHA256SUMS.txt`。候选清单明确记录 `publishable = false`。

## GUI 证据

- maps 最终截图：`output/playwright/v0.13.3-ui/maps-panel-final.png`
- 结构摘要截图：`output/playwright/v0.13.3-ui/structure-source-headings.png`
- 对照图：`output/playwright/v0.13.3-ui/comparison-maps.png`、`comparison-structure-headings.png`
