# DockStart v0.13.4 本地 GUI 候选包构建记录

日期：2026-08-03

## 定位

本轮按人工 GUI 复测需求生成 Basic 与 Assisted 的 MSI/NSIS 本地候选包。此次构建以界面与输入流程修正为主，未执行完整发布门禁；`publishable = false`，不得直接作为公开 Release 发布。

## 本轮调整

- 将项目创建与格式准备统一为按文件实际格式处理的单一路径：受体和配体可分别使用 PDBQT 或原始结构。
- PDBQT 在准备页直接显示就绪并支持按需 3D 预览；PDB/CIF、SDF/MOL 继续提供对应转换操作。
- 移除正常流程中的独立“已有 PDBQT”页面与准备页双模式标签，项目创建后直接进入统一准备页。
- 居中可展开区域的摘要文字，修复运行工作台评分/maps 模块右侧被裁切的问题。
- 关闭项目与路径输入框的浏览器原生历史记录建议。
- 修复“打开项目”请求在项目创建页重新挂载后被重复执行的问题。
- 同步后端、npm、Cargo、Tauri 与界面版本为 `0.13.4`。

## 已执行的快速验证

| 检查 | 结果 |
|---|---|
| TypeScript / Vite 生产构建 | 通过；打包时再次通过两次 |
| 结构输入格式分类测试 | 2/2 通过；覆盖受体/配体独立识别与混合配体输入 |
| Playwright 项目创建页 | 统一输入入口；`clientWidth = scrollWidth = 1728` |
| Playwright 运行工作台 | 文档、评分/maps 卡片及 maps 面板均无水平溢出 |
| Basic 资源准备 | 通过；Vina 1.2.7、Python 3.11.15 |
| Assisted 离线资源准备 | 通过；Meeko 0.7.1、RDKit 2026.3.3；未使用网络 |
| Basic / Assisted Tauri MSI + NSIS 构建 | 通过 |

未按用户要求执行：完整后端测试、`cargo test`、科学功能全量回归、真实安装/卸载和 post-install 门禁。因此本记录不能替代正式发布报告。

## 候选安装包

| Profile | 文件 | 大小（bytes） | SHA256 |
|---|---|---:|---|
| Basic | `DockStart_0.13.4_Basic_x64_en-US.msi` | 24,346,908 | `79af26b25872ac5acade903567c1ce94972e26c3d01b1b9baafa6d6e275e7beb` |
| Basic | `DockStart_0.13.4_Basic_x64-setup.exe` | 18,455,700 | `91575dcdcc86a47007ce7ff4470b9f6059c72480f16e3b3e48f4cc652c383638` |
| Assisted | `DockStart_0.13.4_Assisted_x64_en-US.msi` | 114,151,656 | `eec28a9295bcaecef52d51a44626bc99096c453f094de73873ad4ea189a42d2b` |
| Assisted | `DockStart_0.13.4_Assisted_x64-setup.exe` | 73,826,110 | `15cfc83156c85fb64a3d5ac3599ff36ca031a05163e698292ac5ac446b565d05` |

产物目录：`.release/artifacts/0.13.4/`。校验表：`.release/artifacts/0.13.4/SHA256SUMS.txt`。候选清单明确记录 `publishable = false`。

## GUI 证据

- 统一项目创建页：`output/playwright/v0.13.4-ui/project-create-unified.png`
- 统一格式准备页：`output/playwright/v0.13.4-ui/preparation-unified.png`
- 运行工作台评分/maps：`output/playwright/v0.13.4-ui/run-protocol-overflow.png`
- 对照图：`comparison-unified-input.png`、`comparison-run-protocol.png`、`comparison-disclosures.png`
