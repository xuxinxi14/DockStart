[中文](README.md) · [English](README.en.md)

<p align="center"><img src="apps/desktop/public/dockstart-icon.png" width="88" alt="DockStart 图标"></p>
<h1 align="center">DockStart</h1>
<p align="center">基于 AutoDock Vina 的开源中文本地分子对接工作台</p>
<p align="center">A local Windows GUI for AutoDock Vina with a guided Chinese workflow.</p>
<p align="center">
  <a href="https://github.com/xuxinxi14/DockStart/releases/tag/v1.0.4"><img alt="公开试用版本 v1.0.4" src="https://img.shields.io/badge/public_trial-v1.0.4-155f8a"></a>
  <img alt="平台" src="https://img.shields.io/badge/platform-Windows%2010%20%2F%2011%20x64-1f6feb">
  <a href="LICENSE"><img alt="许可证" src="https://img.shields.io/badge/license-Apache--2.0-2f7d59"></a>
</p>
<p align="center">
  <a href="https://github.com/xuxinxi14/DockStart/releases/tag/v1.0.4">下载 v1.0.4</a> ·
  <a href="docs/quick_start_v1_0_4.md">快速开始</a> ·
  <a href="https://xuxinxi14.github.io/DockStart-Docs/">在线中文文档</a> ·
  <a href="https://github.com/xuxinxi14/DockStart/issues/new/choose">反馈问题</a>
</p>

第一次在 Windows 上做分子对接，可以从 DockStart 的图形界面完成 **PDBQT 导入或准备 → 对接箱体设置 → Vina 运行 → 构象与评分查看 → 实验记录导出**。软件面向 AutoDock Vina 初学者、教学演示和需要保留计算记录的用户，项目与计算保存在本机。

DockStart 调用现有 AutoDock Vina，不修改其搜索算法或评分函数。中文引导帮助处理环境配置、文件格式和运行错误；结构是否合理、搜索区域如何选择，仍需使用者判断。

> 下载与教程基准为 **v1.0.4 Windows x64 公开试用版**。发布页已提供 **Basic 和 Assisted 两种 EXE**；下载信息于 2026-10-07 核对。完整 EXE 安装/升级/卸载、GUI 与全部科学门禁仍未完成，不能称为正式稳定版。后续开发版本的能力以对应发布说明为准。

## 下载：我应该选哪个版本？

| 你的输入 | 推荐下载 | 随包工具 | 安装包大小 |
| --- | --- | --- | ---: |
| 已有受体和配体 PDBQT，只需运行 Vina | [Basic EXE](https://github.com/xuxinxi14/DockStart/releases/download/v1.0.4/DockStart_1.0.4_Basic_x64-setup.exe) | Vina 与精简后端 Python | 17.77 MiB |
| 需要从受体 PDB/CIF、配体 SDF/MOL/单分子 MOL2 尝试准备 PDBQT | [Assisted EXE](https://github.com/xuxinxi14/DockStart/releases/download/v1.0.4/DockStart_1.0.4_Assisted_x64-setup.exe) | Basic 流程及 CPython 3.11、RDKit、Meeko 等准备工具 | 70.59 MiB |

第一次使用原始结构，建议选 **Assisted**；它也可以直接导入已有 PDBQT。普通 Vina 对接无需自行安装 Python 或 AutoGrid4。两种档位共用应用身份，不能并行安装；将项目保存在安装目录之外，升级或切换前备份。

安装包未做 Authenticode 签名，可能显示“未知发布者”。请从本仓库下载并核对 SHA256，不需要关闭安全软件。

<details>
<summary>核对安装包 SHA256</summary>

在 PowerShell 对自己下载的文件执行，例如 Assisted：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath .\DockStart_1.0.4_Assisted_x64-setup.exe
```

```text
56c510d40f61333abda0e42a536d70da4ab581889fd23d4a640b59dd649718b5  DockStart_1.0.4_Basic_x64-setup.exe
c495a08184817aa1619116957def9d0d3b30dc9e2d469e638e04565b95253ee2  DockStart_1.0.4_Assisted_x64-setup.exe
```

[校验文件](docs/release/v1_0_4_SHA256SUMS_2026_10_07.txt) · [下载资产核对记录](docs/release/v1_0_4_downloads_2026_10_07.json) · [发布说明与验证范围](docs/release/v1_0_4_release_notes.md)。哈希不一致时先停止安装并重新核对来源。

</details>

## Quick Start：第一次运行

```text
创建项目 → 导入结构 → 检查/准备 PDBQT → 设置对接箱体与参数
        → 运行前检查 → 运行 Vina → 查看构象与评分 → 导出实验记录
```

1. **检查工具**：安装后打开“工具链”，检测随附 Vina；Assisted 用户同时检查准备工具。
2. **复制示例**：在“帮助与入门”点击“打开示例入口”，把 `basic_pdbqt` 复制到自己的可写目录。
3. **检查箱体与参数**：进入“运行工作台”，保持普通单配体、刚性受体和 Vina 全局对接，检查示例保存的搜索范围。
4. **运行并导出**：通过运行前检查后执行 Vina，到“结果”查看构象与评分，导出 CSV 和 Markdown 实验记录。

详细步骤见 [中文快速开始](docs/quick_start_v1_0_4.md) / [English Quick Start](docs/quick_start_v1_0_4.en.md)。内置小型数据用于熟悉软件；真实体系请按 [1IEP 教程](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/basic-docking-1iep/) 设置有依据的搜索区域，不能套用玩具参数。

## 界面预览

![DockStart v1.0.4 帮助与入门：下一步建议、离线帮助和错误检索](docs/assets/v1.0.4-help.png)

v1.0.4 的实际帮助页：按项目状态提供下一步建议、19 个离线主题和错误码检索。

![DockStart v1.0.3 的 1IEP 教学案例：三维构象与评分表](docs/assets/1iep-result-v1.0.3.webp)

结果截图来自 v1.0.3 的 1IEP 教学案例，用于展示构象与评分查看，不作为 v1.0.4 新一次运行或预测准确率的证据。

## 能做什么，结果如何解释？

- **引导工作流**：项目状态、参数解释、运行前检查和中文错误恢复说明。
- **查看结构与结果**：受体/配体、对接箱体、三维构象和评分表。
- **保存实验记录**：每次运行保留输入快照、配置、工具版本、命令、stdout/stderr、日志、时间、退出码和相关 SHA256。
- **按条件使用进阶协议**：姿势评分、局部优化、有限柔性受体、串行批量、maps、大环及实验性协议，详见 [进阶工作流](docs/advanced_workflows.md)。入口存在不代表全部协议已通过当前安装包验收。

项目、准备、对接和报告在本机进行；主动使用 RCSB/PubChem 结构检索或在线文档时需要网络。表格 RMSD l.b./u.b. 相对本次 Mode 1，不能当作与实验结构的验证 RMSD。

当前不提供 SMILES 自动准备、复杂受体修复、结合口袋预测、PLIP/ProLIF 相互作用分析、分子动力学、PDF 报告或 AI 药效判断。AutoGrid4 是用户自行安装的外部工具；Open Babel、PLIP 和 MGLTools 不随包提供。

**Docking score 仅供结构结合趋势参考，不能替代实验验证。** 输入结构、质子化、电荷、手性、缺失残基、水、金属和搜索区域需要人工复核。更低的分数不能直接证明真实结合、药效、安全性或临床价值。

## 文档与常见问题

| 你遇到的问题 | 入口 |
| --- | --- |
| Windows 安装、第一次运行 | [快速开始](docs/quick_start_v1_0_4.md) |
| PDB/SDF/MOL2 与 PDBQT、受体和配体准备 | [用户指南](docs/user_guide.md) |
| 对接箱体怎么设、Vina 分数与 RMSD 怎么看 | [1IEP 案例](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/basic-docking-1iep/) · [FAQ](docs/faq.md) |
| 找不到 Vina、准备工具检测失败 | [工具链修复](docs/toolchain_repair_guide.md) |
| 使用特殊协议、查阅版本与验证边界 | [进阶说明](docs/advanced_workflows.md) · [发布说明](docs/release/v1_0_4_release_notes.md) |

全部入口见 [仓库文档目录](docs/README.md) 和 [在线中文文档](https://xuxinxi14.github.io/DockStart-Docs/)。

## 源码与贡献

v1.0.4 的功能源码参考为 [`d314084`](https://github.com/xuxinxi14/DockStart/tree/d314084f246ce96cf0f4b56de888843b7c79c500)。原安装包来自保留的 dirty 候选构建，文件哈希与验证范围见 [发布记录](docs/release/v1_0_4_release_notes.md)；源码参考不是对历史打包字节的重建证明。

开发需 Node.js 24.14.1、Rust 1.90.0、Tauri Windows 构建依赖和 Python 3.11+。从源码运行时需要另行配置或装配工具，安装版的随包资源不代表干净源码已经具备完整发布环境。

```powershell
git clone https://github.com/xuxinxi14/DockStart.git
cd DockStart\apps\desktop
npm ci
npm run tauri dev
```

开发与测试步骤、目录用途、提交边界见 [贡献指南](CONTRIBUTING.md)。统一检查入口为根目录 `scripts/check_all.ps1`；[整理前核对的主分支源码 CI](https://github.com/xuxinxi14/DockStart/actions/runs/37024115435) 未通过，部分测试依赖固定 Vina、fixture 原始字节及 Windows 路径条件，仍需修复并重新验证。

仓库主要目录为 `apps/desktop/`、`backend/`、`resources/`、`docs/` 和 `scripts/`。历史 `test/DockStart/` 跟踪了一份运行布局，仅保留作为历史测试资源，不代表当前源码或推荐下载入口；它与未来“不提交 runtime”的资源策略尚待独立整理。

## 反馈与引用

欢迎通过 [问题反馈](https://github.com/xuxinxi14/DockStart/issues/new/choose) 提交使用问题、功能建议或文档改进；中文与英文均可。反馈时附版本、安装档位、复现步骤、错误码及经过检查的日志，删除不打算公开的个人路径与研究数据。

在教学或研究中使用 DockStart，可通过仓库的 **Cite this repository** 或 [CITATION.cff](CITATION.cff) 获取软件引用信息，同时按 [AutoDock Vina 官方说明](https://github.com/ccsb-scripps/AutoDock-Vina#citations) 引用实际使用的对接引擎。目前未登记 DockStart DOI。

DockStart 自有代码采用 [Apache-2.0](LICENSE)；第三方组件分别遵循自己的许可证，详见 [许可证记录](docs/license_notes.md)。[更新记录](CHANGELOG.md) · [路线图](docs/roadmap.md)。
