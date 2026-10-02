# DockStart v1.0.4 快速开始

适用于 Windows 10/11 x64 的 v1.0.4 Assisted 试用安装包。本页先帮助你跑通软件流程，不把玩具示例视为科研验证。

## 1. 下载并核对

打开 [v1.0.4 发布页](https://github.com/xuxinxi14/DockStart/releases/tag/v1.0.4)，下载 `DockStart_1.0.4_Assisted_x64-setup.exe`。目前公开资产只提供 Assisted EXE；已有 PDBQT 也可以直接使用。

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath .\DockStart_1.0.4_Assisted_x64-setup.exe
```

期望值为 `c495a08184817aa1619116957def9d0d3b30dc9e2d469e638e04565b95253ee2`。文件未做代码签名；哈希不一致时先停止安装并重新核对来源。不要关闭安全软件来绕过检测。

正常安装后打开软件，确认显示 v1.0.4。普通对接无需先配置系统 Python；先到工具链页检查随附 Vina 和准备工具。项目目录应独立于软件安装目录。

## 2. 复制基础示例

1. 在默认“帮助与入门”页点击 **打开示例入口**，进入“示例项目（快速体验）”；也可先进入“项目”，再新建示例。
2. 选择基础对接示例 `basic_pdbqt`，指定自己的工作区父目录。
3. 复制后检查受体与配体的准备状态。该示例已有 PDBQT，不需要原始结构转换。

若只想查看界面，可选择 `viewer_result`，它打开已有结果，不会重新运行 Vina。示例内容与用途见 [示例项目](demo_projects.md)。

## 3. 检查对接箱体与参数

进入“运行工作台”，保持普通单配体、刚性受体和 Vina 全局对接。对于内置玩具数据，可先使用示例保存的搜索范围；对于真实结构，必须根据研究依据设置对接箱体，不能套用玩具参数。

检查中心、三轴尺寸（单位 Å）、搜索彻底程度、输出构象数量、能量范围、CPU 和随机种子。悬停问号可阅读说明，支持的问号可点击打开离线帮助。首次运行不需要启用 maps、AutoGrid4、AD4Zn 或水合协议。

## 4. 运行与查看

1. 保存参数，执行运行前检查，处理全部阻塞项。
2. 准备并开始运行，查看真实 Vina 日志；记录本次 `run_id`。
3. 完成后进入“结果”，查看评分表，切换构象。
4. 导出 Markdown 实验记录，查看本次输入快照、配置、工具版本、日志和 `scores.csv`。

`num_modes` 是输出数量上限，返回更少构象不一定是失败。RMSD l.b./u.b. 是相对本次 Mode 1 的差异；Mode 1 的 0/0 不表示与实验构象完全一致。

## 5. 换成真实体系

推荐继续阅读 [1IEP 基础案例](https://xuxinxi14.github.io/DockStart-Docs/docs/part-b/cases/basic-docking-1iep/)。先使用官方准备好的受体/配体 PDBQT 跑通最短路径，再学习 Assisted 的 PDB/CIF + SDF/MOL/单分子 MOL2 准备。记录输入的固定来源、SHA256、工具版本、参数与种子；不要要求分数逐字等于旧教程截图。

## 做不到下一步时

- 无法创建：检查目录可写、磁盘空间和项目名；已有目录不会直接覆盖。
- 不能准备：阅读具体结构警告；缺失残基或 alternate location 的确认不等于软件已修复结构。
- 无法开始：检查运行前阻塞项；缺 RDKit/Meeko 不影响已有 PDBQT 的普通对接。
- 运行失败：保留本次 `stderr.txt`、`log.txt` 和 `metadata.json`，修复后创建新运行，不要删除原记录。
- 文档打不开：离线帮助仍可使用；按失败提示复制地址到浏览器。

详细说明见 [用户指南](user_guide.md)、[FAQ](faq.md) 和 [v1.0.4 发布说明](release/v1_0_4_release_notes.md)。

Docking score 仅供结构结合趋势参考，不能替代实验验证。
