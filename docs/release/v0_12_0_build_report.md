# DockStart v0.12.0 Windows 构建报告

构建日期：2026-07-26  
作者 / 发布者：XinXi Xu  
目标平台：Windows x86_64

## 版本一致性

后端、`package.json`、`package-lock.json`、Cargo、Tauri 与前端导航版本均为
`0.12.0`。

## 自动化与科学回归

- Python：466 项测试通过。
- Rust/Tauri：20 项测试通过。
- 前端：TypeScript 与 Vite 生产构建通过。
- GUI：使用 Playwright 在 1600 × 1000 与 1100 × 760 视口检查
  AutoDock4 maps 工作台；无布局遮挡、横向溢出或页面控制台错误。
- 官方 AD4 回归：使用 Scripps AutoDock 4.2.6 `1dwd` 示例重新生成
  60 × 60 × 60、spacing 0.375 Å 的 maps，并由随附 Vina 1.2.7 以
  `--scoring ad4` 完成 DockStart 全流程；固定 seed 12345 下最佳评分
  -11.55 kcal/mol。
- Basic 打包后门禁：两次真实 Vina 运行均完成，运行快照、评分 CSV 与
  Markdown 报告齐全。
- Assisted development 与 post-package 门禁：内置 Python 3.11.15、
  RDKit 2026.3.3、Meeko 0.7.1、Gemmi 与 Vina 1.2.7 均通过离线检测；
  CIF 受体、SDF 配体准备和真实对接完成。

## 安装包

### Basic Stable

| 安装包 | 大小 | SHA256 |
|---|---:|---|
| `DockStart_0.12.0_Basic_x64_en-US.msi` | 23,571,241 B | `eec98783c79384d3df83a21d81b3d78255534cc22002b03df297fc3aff69ca1b` |
| `DockStart_0.12.0_Basic_x64-setup.exe` | 17,959,354 B | `adce5fbc1adaa42b91d48b6108d97b3a3d77ff9fb147c0f08b26a0663e995ab4` |

### Assisted Stable

| 安装包 | 大小 | SHA256 |
|---|---:|---|
| `DockStart_0.12.0_Assisted_x64_en-US.msi` | 113,375,989 B | `ab6eeadd600c127fcfb989b69f2a2c6687fc24386b798e56000fbf1eac5bbf4a` |
| `DockStart_0.12.0_Assisted_x64-setup.exe` | 73,335,769 B | `a604dd55d61db8147f53e6ec5d4baa36e5b6a4e39d20eaf6ba89a7a90a48192a` |

Basic 与 Assisted staged resources 均未发现 `autogrid4.exe` 或
AutoDockSuite 安装程序；只包含 DockStart 自有的 adapter 与 maps 工作流代码。

## 发布门禁状态

- Basic：构建、打包后真实双次对接和产物校验通过。
- Assisted：development、自动化测试、构建、post-package 离线准备与对接通过。
- Assisted post-install：未执行覆盖安装。门禁检测到本机已有
  `C:\Users\19701\AppData\Local\DockStart` 安装和卸载注册项后按设计拒绝继续；
  构建脚本没有覆盖或卸载现有软件。因此 Assisted 安装包已经生成，但 manifest
  仍为 `publishable=false`，不得在完成干净 Windows 账户/设备的真实安装与卸载门禁前
  标记为正式可发布产物。

## 已知限制

- AutoGrid4 是用户自行安装和配置的外部 GPL 工具，不随两个安装包分发。
- v0.12.0 的 AD4 maps 协议只开放标准非金属、刚性受体、单配体流程。
- AD4Zn、柔性受体 AD4、批量 AD4 maps 和水合对接未开放。
- 安装包尚未进行 Authenticode 签名。

