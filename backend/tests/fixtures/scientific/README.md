# Scientific regression fixtures

这些文件只用于 DockStart 的真实工具链回归，不会进入 Basic/Assisted 安装包，也不能单独证明对接结果具有实验意义。

其中 `flexible_mmcif_identity` 是 DockStart 自行构造的最小格式/身份映射
fixture，不是实验结构；其用途和字段覆盖见该目录的 `README.md`。
`flexible_mmcif_1h4w` 只保存 RCSB 1H4W 的来源、SHA256 与预期身份事实，
不提交第三方坐标文件；真实文件由验证者显式提供。

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
- `flexible_ad4_1fpu` 是 1FPU / 1IEP 有限柔性标准 AD4 的离线外部验收合同，不提交 maps 或外部工具。
- `multiple_ligands_ad4_5x72` 是固定顺序 P59 + P69 联合 AD4 的离线外部验收合同，只接受联合评分。
- `serial_screening_ad4` 是 12 项固定工作负载、单一冻结 maps、取消/恢复/失败续跑/归档导出的离线可靠性门禁。
- `flexible_mmcif_identity` 用于验证 author/label 残基身份、插入码、altloc、occupancy、model 与 Gemmi PDB 桥接。
- `flexible_mmcif_1h4w` 用于在外部真实复杂 mmCIF 上复核同一身份链，并将 Meeko 不兼容明确报告为阻断。
- `macrocycle_bace1` 用于验证 Meeko 大环自动断环、刚性对照、Vina/CLI 一致性和拓扑安全导出。
- Golden hash 只适用于 manifest 写明的工具链版本和输入；工具版本变化后必须重新做科学审阅，不能直接刷新 hash。
