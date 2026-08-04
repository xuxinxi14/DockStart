# DockStart v0.13.8：六个 AutoDock Vina 官方示例人工验收

v0.13.8 沿用 v0.13.7 已验证的六个官方示例科学流程与参数，主要更新格式转换页、批量配体预览、大环审查界面和 AutoGrid4 配置入口。

完整逐键操作与官方输入清单见：

- [DockStart v0.13.7：六个 AutoDock Vina 官方示例人工验收](manual_official_vina_examples_v0_13_7.md)

本轮人工复核时额外确认：

1. 5X72 批量输入的每个配体均显示来源名称，并可在准备页逐项切换 3D 预览；
2. BACE_1 大环候选选择不出现矩形输入框，确认与继续动作按四步流程互斥显示；
3. AutoGrid4 可从“工具链”页进入设置页配置，缺失时不影响普通 Vina 或 Assisted 准备能力；
4. 自动准备与 docking score 仍需人工检查，不能替代实验验证。
