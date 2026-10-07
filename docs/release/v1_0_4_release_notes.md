# DockStart v1.0.4 · Windows x64 试用版（Basic / Assisted）

DockStart 是基于 AutoDock Vina 的开源中文桌面工作台，整理结构准备、对接箱体、参数、任务运行、构象查看和实验记录。v1.0.4 重点更新帮助与入门；不改变 Vina 搜索/评分算法，不新增运行依赖或项目数据 schema。

**当前为公开试用版本。完整 EXE 安装/升级/卸载、桌面 GUI 及全部科学发布门禁仍待完成，不声明正式 Stable Release。**

## 下载

截至 2026-10-07，发布页提供 Basic 与 Assisted 两种 EXE。Basic 在 2026-10-05 新增为公开资产；Assisted 仍是原公开文件，亦可直接使用已有 receptor/ligand PDBQT。MSI 不列作本次公开下载。

| 文件 | 大小 | SHA256 |
| --- | ---: | --- |
| [DockStart_1.0.4_Basic_x64-setup.exe](https://github.com/xuxinxi14/DockStart/releases/download/v1.0.4/DockStart_1.0.4_Basic_x64-setup.exe) | 18,630,013 bytes（约 17.77 MiB） | `56c510d40f61333abda0e42a536d70da4ab581889fd23d4a640b59dd649718b5` |
| [DockStart_1.0.4_Assisted_x64-setup.exe](https://github.com/xuxinxi14/DockStart/releases/download/v1.0.4/DockStart_1.0.4_Assisted_x64-setup.exe) | 74,017,034 bytes（约 70.59 MiB） | `c495a08184817aa1619116957def9d0d3b30dc9e2d469e638e04565b95253ee2` |

支持 Windows 10/11 x64。安装包未做 Authenticode 签名，可能显示“未知发布者”；请从本项目发布页下载并核对 SHA256。安装元数据的 publisher 为 XinXi Xu，不等于数字签名身份。

两包当前信息见 [2026-10-07 校验文件](v1_0_4_SHA256SUMS_2026_10_07.txt) 与 [下载资产核对清单](v1_0_4_downloads_2026_10_07.json)。

原 [2026-10-02 SHA256SUMS](https://github.com/xuxinxi14/DockStart/releases/download/v1.0.4/v1_0_4_SHA256SUMS.txt) 与 [历史构建及补充验证清单](https://github.com/xuxinxi14/DockStart/releases/download/v1.0.4/v1_0_4_release_manifest.json) 按当时仅公开 Assisted 的状态保留。新增 Basic 资产与下载核对记录不改变历史门禁结果，也不作为新增安装或科学验收通过项。

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath .\DockStart_1.0.4_Assisted_x64-setup.exe
```

## v1.0.4 改进

- 帮助中心根据实际项目和工具状态给出下一步建议。
- 19 个随包离线主题、本地关键词检索、错误码分类与排查步骤。
- 支持的问号可点击查看离线详细说明。
- 显式在线文档搜索和章节链接，浏览器打开失败时保留地址复制入口。
- 设置页可保存启动页偏好，下次打开帮助或项目页。

## 使用路径与工具边界

已有 PDBQT 可直接导入 Basic 或 Assisted。Basic 随附 AutoDock Vina 1.2.7 与精简后端 CPython 3.11；Assisted 额外随附 RDKit 2026.3.3、Meeko 0.7.1 等固定准备工具，可离线尝试从受体 PDB/CIF、配体 SDF/MOL/单分子 MOL2 准备 PDBQT。普通流程无需自行安装系统 Python。用户配置的兼容 preparation Python 仍优先；自动准备结果需要人工检查。

AutoGrid4 是用户自行安装的外部工具，普通 Vina/Vinardo 实时对接可以跳过。Open Babel、PLIP、MGLTools 不随包提供。SMILES、配体 PDB 自动准备、多记录/批量 MOL2、口袋预测、复杂受体修复、PLIP/ProLIF 分析、分子动力学、PDF 报告和 AI 药效判断不在当前能力范围。

本地计算、准备和项目记录可离线使用；主动搜索/下载 RCSB/PubChem 原始结构或打开在线文档时需要网络。报告、诊断及项目记录可能包含本机路径，分享前检查。

## 源码对应关系与历史构建

v1.0.4 功能源码参考：[`d314084f246ce96cf0f4b56de888843b7c79c500`](https://github.com/xuxinxi14/DockStart/tree/d314084f246ce96cf0f4b56de888843b7c79c500)。

原 `v1.0.4` 标签误指向 v1.0.3 提交 `36249f328ef4de231469444ec4638680c93b897e`。2026-10-02 已将标签修正到上述 v1.0.4 提交；已下载旧 Source code 的用户应重新获取并核对提交。本次保留原 Assisted EXE，不重新构建或替换其字节。

该 EXE 的历史候选身份是 `1.0.4-36249f32-20260930T201519Z-help-dirty`，由上述旧提交加未提交修改构建；原 manifest 的 `dirty_worktree=true`、`publishable=false` 和未运行门禁记录保留。功能提交对应不等于对历史安装包逐字节重建的证明，不能追认原候选为已通过完整发布门禁。

## 验证范围（2026-10-02）

| 检查 | 结果与范围 |
| --- | --- |
| v1.0.4 功能提交前端 | 150 项测试通过；TypeScript/Vite 构建通过 |
| npm 锁文件审计 | 当次 registry 审计无已知告警；不覆盖 Python/Rust |
| Rust | 格式检查通过；本次未跑 check/test/clippy |
| 后端首轮全量 | 1305 项，3 failure、4 error、15 skipped；缺固定 Vina 与 fixture 换行差异造成条件失败 |
| 后端相关模块复验 | 在审查副本补齐 Vina并恢复三份 fixture 的 Git 原始字节后，53 项复验无失败、1 项跳过；未在该修复副本重跑全量 |
| 主仓库 Windows CI | [文档发布提交的 CI](https://github.com/xuxinxi14/DockStart/actions/runs/37022720578) 未通过：1305 项，101 failure、44 error、15 skipped；不能用本地复验替代该结果 |
| Basic MSI 内容 | 实际 Vina、两轮对接、评分解析、快照与 Markdown 报告通过 |
| Assisted MSI 内容 | 固定 runtime、CIF/配体准备、Python 优先级与真实 Vina 对接通过 |
| 依赖许可资源 | 两种 MSI 中许可包完整性检查通过；不是法律认证 |
| 安装文件身份 | 四份本地候选大小和 SHA256 与原 manifest 一致；公开 EXE digest 与本地 Assisted EXE 一致 |

MSI 内容提取验证不是 EXE 的真实安装、升级、卸载或完整 GUI 验收；使用小型示例也不能代表科研准确率。v1.0.0 安装门禁记录及其他历史科学验收不计作 v1.0.4 通过项。已补充核对结果保存在发布验证清单，原候选 manifest 不回写。

本次 CI 提交只变更文档与截图，应用/后端/测试源码未改。失败日志包含固定 `resources/vina/vina.exe` 缺失、科学 fixture 字节校验及 Windows 临时路径差异；尚未逐项排除其他原因。CI 在 Python 测试阶段终止，后续前端和 Rust 门禁未执行，仍需独立修复发布环境并重新验证。

## 已知限制和待验收

- 当前产物在无开发环境依赖的 Windows 10/11 环境下安装、GUI 主流程、取消、重开项目与卸载仍待验收。
- 需要逐字节校验的 fixture 应保持声明的原始字节；换行转换会让科学契约失效。
- 部分外部验收测试依赖固定 Vina，干净源码未准备资源时不保证全量测试直接通过。
- 在线操作案例含明确标注的旧版截图，不能视为本次安装包的新实跑记录。
- Beta/Experimental 及其他高级协议按前置条件使用；不声明全部通过当前包的科学安装态门禁。
- Basic/Assisted 共用应用身份，不能并行安装。更新前备份独立项目目录；当前没有自动更新。

## 开始使用与反馈

- [快速开始](https://github.com/xuxinxi14/DockStart/blob/main/docs/quick_start_v1_0_4.md)
- [用户指南](https://github.com/xuxinxi14/DockStart/blob/main/docs/user_guide.md)
- [在线安装说明](https://xuxinxi14.github.io/DockStart-Docs/docs/part-c/install-update-data-safety/)
- [1IEP 案例](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/basic-docking-1iep/)
- [反馈问题](https://github.com/xuxinxi14/DockStart/issues)：附版本、档位、步骤和经过检查的日志。
- [第三方许可证](https://github.com/xuxinxi14/DockStart/blob/main/docs/license_notes.md)：DockStart 本体 Apache-2.0，第三方按各自条款分发。

**Docking score 仅供结构结合趋势参考，不能替代实验验证。** 软件不保证真实结合、药效、安全性或临床价值。
