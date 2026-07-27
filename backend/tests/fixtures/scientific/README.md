# Scientific regression fixtures

这些文件只用于 DockStart 的真实工具链回归，不会进入 Basic/Assisted 安装包，也不能单独证明对接结果具有实验意义。

## 来源和许可证

- 上游项目：AutoDock Vina `v1.2.7`
- 上游地址：https://github.com/ccsb-scripps/AutoDock-Vina
- 上游许可证：Apache License 2.0
- 取用目录：`example/flexible_docking` 和 `example/docking_with_macrocycles`
- 结构标识：1FPU 与官方 BACE_1 示例；结构数据仍应保留原始数据库和论文归属。

`macrocycle_bace1/BACE_1_ligand.sdf` 是为了适配 DockStart 当前支持的
SDF/MOL 输入，由本地外部 Open Babel 2.3.2 从上游
`BACE_1_ligand.mol2` 机械转换得到。原始 MOL2 一并保留，两个文件的
SHA256 和转换说明见对应 `fixture_manifest.json`。Open Babel 不是
DockStart 运行时依赖，也不会随安装包分发。

## 使用边界

- `flexible_1fpu` 用于验证默认严格失败、坏残基清单审阅以及明确确认后的 `--allow_bad_res`。
- `macrocycle_bace1` 用于验证 Meeko 大环自动断环、刚性对照、Vina/CLI 一致性和拓扑安全导出。
- Golden hash 只适用于 manifest 写明的工具链版本和输入；工具版本变化后必须重新做科学审阅，不能直接刷新 hash。
