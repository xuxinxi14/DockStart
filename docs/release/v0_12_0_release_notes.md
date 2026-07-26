# DockStart v0.12.0

v0.12.0 完成 AutoDock4 maps 基础设施，并继续提供 Basic Stable 与 Assisted Stable 两个
Windows x64 profile。

## 主要变化

- 对接工作台新增独立 `AutoDock4（maps）` 协议；
- 支持生成 GPF、调用外部 AutoGrid4、导入已有 maps、检查缺失原子类型与文件；
- maps manifest 保存受体、配体、Box、网格、命令、日志、版本和 SHA256；
- 受体、Box、maps 文件或配体原子类型变化后，运行前检查会阻止使用失效 maps；
- 每次 AD4 run 保存不可变 maps 快照，并通过 Vina 1.2.7 的 `--maps` 与 `--scoring ad4` 执行；
- AD4 项目结果写入 `ad4_scores.csv` 与 `ad4_docking_report.md`，不覆盖标准 Vina 文件；
- 结果页、运行历史和报告明确标记协议，禁止把 AD4 分值与 Vina/Vinardo 直接比较；
- Help 成为初始页面；长耗时导入、搜索和准备操作增加等待窗口；非线性导航和 Assisted Meeko 冷启动检测得到修复。

## AutoGrid4 安装边界

AutoGrid4 4.2.6 按 GNU GPL 提供。DockStart v0.12.0 的 Basic/Assisted 安装包均不内置它。
需要生成 maps 的用户应自行安装 AutoGrid4，并在“设置 → 工具路径”中配置
`autogrid4.exe`。导入并通过校验的 maps 可以在未安装 AutoGrid4 时继续用于运行。

## 科学回归

2026-07-26 使用 Scripps 官方 AutoDock 4.2.6 `1dwd` 示例完成真实回归：

- AutoGrid4 4.2.6：60 × 60 × 60，spacing 0.375 Å，完整 maps；
- DockStart：`ad4_001` manifest、不可变 run 快照、`run_001` 完成；
- AutoDock Vina 1.2.7：`ad4` 评分，seed 12345，最佳评分 -11.55 kcal/mol；
- 输出：`results/ad4_scores.csv`、`reports/ad4_docking_report.md`。

该回归只证明标准非金属刚性受体链路可以复现，不证明预测的真实结合或药效。

## 当前不包含

- AD4Zn 或其他金属专用参数；
- 柔性受体 AD4 maps；
- 批量 AD4 maps；
- 水合对接；
- 分子动力学、相互作用自动结论或药效判断。

## Windows 安装包

四个 Windows x64 安装包已生成，文件名、大小、SHA256 与门禁结果见
[`v0_12_0_build_report.md`](v0_12_0_build_report.md)。

Basic 已通过打包后真实运行回归。Assisted 已通过 development 与 post-package
离线准备/对接门禁；由于构建机已有 DockStart 安装，安全门禁没有覆盖或卸载现有软件，
其 post-install 状态仍为待在干净 Windows 账户或设备复核。
