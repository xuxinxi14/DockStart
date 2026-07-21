# DockStart v0.11.2 Windows 构建报告

构建日期：2026-07-22  
作者/发布者：XinXi Xu  
目标平台：Windows x86_64

## 版本一致性

后端、`package.json`、`package-lock.json`、Cargo、Tauri 与前端导航均为 `0.11.2`。最终桌面可执行文件的 ProductVersion、FileVersion 均为 `0.11.2`，CompanyName 为 `XinXi Xu`。

## Basic Stable

状态：构建与打包后回归通过。

- Python 全量测试：456 项通过。
- 前端生产构建与 Cargo 检查通过。
- 随附 Python 3.11.15 与 AutoDock Vina 1.2.7。
- 不包含 RDKit、Meeko、NumPy、SciPy 或 Python `site-packages`。
- 打包后执行两轮真实 Vina 对接，均生成配置、快照、构象、评分与 Markdown 报告。

| 安装包 | 大小 | SHA256 |
|---|---:|---|
| `DockStart_0.11.2_Basic_x64_en-US.msi` | 23,530,013 B | `44f8e77e7e13367da4f1fcd5431753a72fffbe08ac52eacaa92dfdbdbbe3f661` |
| `DockStart_0.11.2_Basic_x64-setup.exe` | 17,918,914 B | `387e5f2c2bfe888256881585da39912c0441fd155bf21c722c7a2f6f80f13867` |

## Assisted Stable

状态：安装包已生成；development 与 post-package 门禁通过，post-install 门禁未通过，因此当前清单标记 `publishable=false`。

- 固定工具链装配通过：Python 3.11.15、RDKit 2026.3.3、Meeko 0.7.1、AutoDock Vina 1.2.7。
- 断网 development-layout 与 post-package 流程均完成 CIF 受体、SDF 配体准备和真实 Vina 对接。
- Python 全量测试 456 项、Cargo 19 项及前端生产构建通过。
- post-install 门禁安全拒绝执行，因为本机已存在 `C:\Users\19701\AppData\Local\DockStart` 安装及卸载注册项；构建脚本未覆盖或卸载现有软件。

| 安装包 | 大小 | SHA256 |
|---|---:|---|
| `DockStart_0.11.2_Assisted_x64_en-US.msi` | 113,334,761 B | `dcd3f774115c08afbaf473f2f5e35213e04dad62394cc011857fad94d27697c4` |
| `DockStart_0.11.2_Assisted_x64-setup.exe` | 73,306,538 B | `d437612f12c820f9bb11839698cfbdf82ff3eb11445c80a21053b0374e579750` |

## 发布前剩余门禁

在无现有 DockStart 安装的隔离 Windows 账户或干净设备上，对 Assisted NSIS 执行真实安装、安装目录流程验证及静默卸载检查。该门禁通过前，不应把 Assisted 安装包标记为正式可发布产物。

