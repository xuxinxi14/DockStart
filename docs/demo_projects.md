# DockStart Demo Projects

DockStart V0.8.2 开始提供小型示例项目，用于第一次打开软件时理解流程。

示例项目只用于软件流程演示，不用于科研结论、真实 docking 解释、药效判断、课程或论文中的科学证据。

## 示例类型

### Basic Mode 示例

目录：

```text
examples/demo_basic_project/
```

包含：

- `prepared/receptor.pdbqt`
- `prepared/ligand.pdbqt`
- `project.json`

用途：

- 演示已有 PDBQT 的最低依赖路径；
- 帮助用户理解导入 PDBQT、设置 Box、生成配置、准备运行记录的流程；
- 不需要 RDKit / Meeko。

限制：

- PDBQT 是玩具文件；
- 不代表真实受体或配体；
- 不用于真实 docking 结论。

### Assisted Mode 示例

目录：

```text
examples/demo_assisted_project/
```

包含：

- `raw/receptor_demo.pdb`
- `raw/ligand_demo.sdf`
- `project.json`

用途：

- 演示 raw 文件存在但 prepared PDBQT 尚未生成时的状态；
- 帮助用户理解 RDKit / Meeko 工具链为什么只影响 Assisted Mode；
- 可用于测试 PreparationPage 的入口和错误提示。

限制：

- raw 文件是玩具数据；
- 自动准备是否能成功取决于本机 RDKit / Meeko 能力；
- 即使生成 PDBQT，也不代表科学正确。

## 如何使用

1. 打开 DockStart。
2. 进入“创建 / 打开项目”。
3. 选择一个保存目录。
4. 在“示例项目”区域复制 Basic 或 Assisted 示例。
5. 按页面提示继续导入、准备、设置 Box 或查看 3D。

DockStart 会把示例复制到你的保存目录，不会直接修改仓库内的 `examples/` 模板。

## 为什么不提供真实大型示例

当前目标是降低使用门槛，而不是提供科研数据集。真实受体、配体、docking 输出和大型结构文件会带来：

- 许可证和来源确认成本；
- 文件体积增加；
- 用户误把演示结果当科研结论的风险；
- Git 仓库膨胀。

因此 V0.8.2 只提交小型玩具示例，并明确标注演示边界。
