# 手动 PDBQT 准备指南

本文档说明 DockStart 当前如何理解 `raw/` 文件和 `prepared/` PDBQT 文件，以及用户在自动准备能力尚未覆盖所有场景时应如何安排工作流。

V0.3.2 可以在检测到可用 Python + RDKit + Meeko 时，尝试把 ligand SDF/MOL raw 文件准备为 `prepared/ligand.pdbqt`。V0.3.3 可以在检测到可用 Python + Meeko receptor CLI 时，尝试把 receptor PDB/CIF raw 文件准备为 `prepared/receptor.pdbqt`。DockStart 当前仍不会自动处理 ligand MOL2 或 SMILES，也不会接入 Open Babel、PLIP 或 MGLTools。

V0.3.9 真实工具链验收使用独立 `dockstart-rdkit-meeko` conda 环境完成。建议用户不要直接把 RDKit/Meeko 安装进 Microsoft Store Python，而是在 conda/mamba 环境中安装 `python=3.11 rdkit meeko numpy scipy`，再通过 DockStart 设置页配置该环境的 `python.exe`。

## 1. 什么是 raw 文件

raw 文件是从结构数据库或其他来源得到的原始结构文件。DockStart 当前支持保存的典型 raw 文件包括：

```text
raw/receptor_1HSG.pdb
raw/receptor_1HSG.cif
raw/ligand_2244.sdf
raw/ligand_name_aspirin.sdf
```

raw 文件用于记录来源和保留原始数据。它们不是 AutoDock Vina 当前可以直接使用的输入。

## 2. 什么是 prepared PDBQT

prepared PDBQT 是已经完成 docking 输入准备后的文件。DockStart 当前运行 Vina 时需要：

```text
prepared/receptor.pdbqt
prepared/ligand.pdbqt
```

`project.json` 中：

- `receptor.raw_file` / `ligand.raw_file` 记录原始结构；
- `receptor.file` / `ligand.file` 记录 Vina 实际使用的 prepared PDBQT。

## 3. 为什么 Vina 需要 PDBQT

AutoDock Vina 使用 PDBQT 作为受体和配体输入格式。PDBQT 相比普通 PDB/SDF 包含 docking 所需的额外信息，例如原子类型、电荷和可旋转键相关信息。

因此，下载到 PDB、CIF 或 SDF 后，通常还需要准备步骤，才能得到 Vina 可用的 PDBQT。

## 4. 为什么下载 PDB/SDF 后还不能直接运行 Vina

RCSB PDB 的 PDB/CIF 文件通常是结构坐标来源，PubChem 的 SDF 文件通常是配体结构来源。它们仍可能需要：

- 选择合适链或模型；
- 删除不需要的水、离子或配体；
- 补充氢原子；
- 处理电荷；
- 为配体设置可旋转键；
- 写出 PDBQT。

这些步骤涉及化学和结构判断。DockStart V0.3.3 只对 ligand SDF/MOL 和 receptor PDB/CIF 提供最小自动准备辅助；MOL2/SMILES 和复杂结构修复仍需用户使用外部工具或后续版本。DockStart 不自动判断准备结果是否科学合理。

V0.3.6 文档化后的推荐路径是：下载 raw 文件，检查 RDKit/Meeko 能力，尝试准备 ligand/receptor PDBQT，人工检查结果，再继续 Box、Vina config、运行、解析和 Markdown 报告。自动准备是辅助步骤，不是科学验证。

## 5. DockStart V0.3.2 ligand 自动准备范围

PreparationPage 的“准备 ligand PDBQT”按钮会：

- 读取 `project.json` 中的 `ligand.raw_file`；
- 仅接受 SDF 或 MOL；
- 使用当前解析到的 Python + RDKit + Meeko；
- 输出 `prepared/ligand.pdbqt`；
- 默认不覆盖已有 `prepared/ligand.pdbqt`；
- 保存 stdout、stderr 和 metadata 到独立记录目录，例如 `preparation/ligand_001/`。

当前不会：

- 自动处理 MOL2 或 SMILES；
- 保证质子化、电荷、构象、互变异构或手性一定科学正确；
- 判断 docking 结果是否具有药效意义。

## 6. DockStart V0.3.3 receptor 自动准备范围

PreparationPage 的“准备 receptor PDBQT”按钮会：

- 读取 `project.json` 中的 `receptor.raw_file`；
- 接受 PDB 或 CIF；
- 使用当前解析到的 Python + Meeko receptor CLI；
- 输出 `prepared/receptor.pdbqt`；
- 默认不覆盖已有 `prepared/receptor.pdbqt`；
- 保存 stdout、stderr 和 metadata 到独立记录目录，例如 `preparation/receptor_001/`。

当前不会：

- 接入 MGLTools 或 Open Babel 作为兜底；
- 自动修复缺失残基；
- 自动判断金属离子、水分子、辅因子或链选择是否应该保留；
- 保证质子化状态适合当前体系；
- 判断 docking 结果是否具有药效意义。

注意：V0.3.9 验收中 Meeko `mk_prepare_receptor.py` 需要 `pkg_resources`。如果本地环境使用较新的 `setuptools` 后出现 `No module named 'pkg_resources'`，可在独立 conda 环境中安装 `setuptools<81` 作为兼容处理。

## 7. 可选外部工具

用户可以根据课程、实验室规范或教程选择外部工具准备 PDBQT。常见选择包括：

- Meeko：常用于基于 Python 的 Vina 输入准备；
- AutoDockTools / MGLTools：传统 AutoDock 工作流中常见的 PDBQT 准备工具；
- Open Babel：常用于格式转换，但许可证和结果适用性需要谨慎确认。

DockStart V0.3 可以在工具链能力可确认时尝试使用 RDKit/Meeko 自动准备部分 PDBQT，但不会自动安装这些工具，也不会保证自动准备结果科学正确。

## 8. 许可证注意事项

当前 DockStart 不内置以下工具：

- Open Babel；
- MGLTools；
- PLIP。

原因包括许可证、分发方式、依赖体积和维护成本都需要单独评估。

v0.10.2 Basic Stable 不内置 Meeko/RDKit；Assisted Stable 则把固定版本作为独立、可替换的 Python runtime 随包提供。源码仓库不提交 runtime 二进制或 wheel，发布时从固定离线 wheelhouse 装配并校验 SHA256。应用运行时不会联网安装这些包或修改系统 Python。Meeko 的 LGPL、对应源码、依赖来源、包体积和升级兼容性仍需在每次版本更新时重新审查。

## 9. DockStart 当前不保证外部 PDBQT 的科学正确性

如果用户使用外部工具生成 PDBQT，DockStart 当前只做基础文件检查和导入，不判断：

- 受体是否选择了正确链；
- 配体质子化状态是否合理；
- 电荷是否正确；
- 可旋转键是否合适；
- docking box 是否覆盖合理结合区域；
- 生成的 PDBQT 是否足以支持科研结论。

Docking score 仅供结构结合趋势参考，不能替代实验验证。

## 10. 后续扩展自动准备的要求

如果 DockStart 后续扩展 raw → prepared PDBQT 自动准备，例如支持更多格式、更多 Meeko 版本路径或内置离线工具链，需要单独完成：

- 清晰的受体准备流程设计；
- 清晰的配体准备流程设计；
- RDKit/Meeko 或其他工具的 adapter 边界；
- 输入检查和错误恢复；
- 可复现的参数记录；
- 单元测试和示例数据；
- 许可证和第三方依赖审查；
- 面向初学者的中文错误解释。

在这些条件满足前，DockStart 会继续把 raw 下载、手动准备和 prepared PDBQT 导入保持为不同步骤。
