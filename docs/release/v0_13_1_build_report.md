# DockStart v0.13.1 本地 GUI 候选包构建记录

构建日期：2026-08-01  
分支：`main`  
用途：供当前科学工作树的真实 GUI 人工体验；不是公开 Release。

## 版本与内容

后端、npm、Cargo、Tauri 和界面显示等七处版本已统一为 `0.13.1`。本候选包含当前 `main` 与工作树中的科学证据收口增量、结果页亮色主题对比度修复和人工体验清单。

AutoGrid4 与 `AD4Zn.dat` 仍不随 Basic 或 Assisted 分发。水合 AD4、多配体共同对接和 AD4Zn 继续保持 Experimental/Beta 边界。

## 构建门禁

| 项目 | 结果 |
| --- | --- |
| 后端 unittest | `1193` 通过，`5` 跳过；Basic/Assisted 各执行一次 |
| TypeScript/Vite 生产构建 | 通过 |
| Cargo check | 通过 |
| Rust/Tauri 测试 | `32/32` 通过（Assisted） |
| Basic 资源与打包后 Vina 回归 | 通过；两次真实运行均完成 |
| Assisted development-layout 回归 | 通过；离线 CIF/SDF 准备、Vina、报告完成 |
| Assisted post-package 回归 | 通过；随包 RDKit `2026.3.3`、Meeko `0.7.1`、Vina `1.2.7` |
| Assisted post-install 回归 | 阻塞；检测到现有 `C:\Users\19701\AppData\Local\DockStart`，安全门禁拒绝覆盖 |

Assisted 安装包已生成并通过打包布局检查，但因未完成隔离环境安装/卸载门禁，`artifact-manifest.json` 保持 `publishable=false`。这不影响本机人工安装测试，但不能将其作为可发布证据。

## 安装包与 SHA256

| Profile | 文件 | 字节 | SHA256 |
| --- | --- | ---: | --- |
| Basic | `DockStart_0.13.1_Basic_x64_en-US.msi` | 24,310,044 | `6b03e61857e7a56d51d93c2db0bb22bd6b239fef1452c5ad3261ba611d1552cc` |
| Basic | `DockStart_0.13.1_Basic_x64-setup.exe` | 18,439,755 | `cbcd5b1914ddcacb0a39b0cc9ee5d5b9cc7241cd22d9209a05758c438226e52b` |
| Assisted | `DockStart_0.13.1_Assisted_x64_en-US.msi` | 114,122,984 | `eac7261199c64930222eab6915bdd3fcf1385412842526918b50e1606a9e4062` |
| Assisted | `DockStart_0.13.1_Assisted_x64-setup.exe` | 73,814,647 | `273d58c45a87051996de60df14d9b13ad76e63445417c104d1b19236add67968` |

产物目录：`.release/artifacts/0.13.1/`。独立校验表：`.release/artifacts/0.13.1/SHA256SUMS.txt`。

## 人工测试说明

安装前请先正常退出并卸载当前 DockStart，或使用隔离 Windows 账户/虚拟机，避免覆盖旧安装状态。建议优先测试 Assisted NSIS，再按 `docs/manual_ui_acceptance_current_worktree.md` 执行 P0；失败时保留编号、截图、项目目录和日志路径。
