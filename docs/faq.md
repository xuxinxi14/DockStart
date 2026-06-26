# DockStart FAQ

## 1. 为什么找不到 Vina？

常见原因：

- AutoDock Vina 没有安装；
- `vina` / `vina.exe` 没有加入 PATH；
- DockStart 设置页中填写的 Vina 路径不正确；
- Windows 路径中选到了目录，而不是 `vina.exe` 文件。

处理建议：

- 在命令行运行 `vina --version` 或 `vina.exe --version`；
- 如果命令行找不到，回到 DockStart 设置页填写 `vina.exe` 的完整路径；
- 保存设置后重新运行工具检测。

## 2. 为什么 Meeko/RDKit 显示 missing？

V0.1 只检测 Meeko/RDKit 是否存在，不使用它们自动处理分子结构。显示 missing 不影响本地 PDBQT docking MVP，只要你已经准备好 `receptor.pdbqt` 和 `ligand.pdbqt`，并且 Vina 可用，就可以继续 V0.1 流程。

Meeko/RDKit 自动处理会在后续单独设计、测试并审查许可证；当前 V0.2 raw workflow 仍不调用它们处理分子。

## 3. 为什么现在只支持 PDBQT？

AutoDock Vina 的核心输入是 PDBQT。V0.1 的目标是先跑通最小闭环，所以只接受用户已经准备好的 `receptor.pdbqt` 和 `ligand.pdbqt`。

PDB、SDF、MOL2 到 PDBQT 的自动转换需要额外工具和更复杂的化学处理逻辑，会在后续版本中谨慎加入。

## 4. docking score 越低是否代表药效越好？

不能这样解释。

Docking score 只表示在特定输入结构、box、参数和 Vina 版本下的计算结果。它可以作为结构结合趋势参考，但不能证明真实结合能力、药效、安全性或临床价值。任何药效判断都需要实验验证和更完整的研究流程。

DockStart 不会自动判断“药效好/不好”，也不会输出“候选药物”结论。

## 5. PDB/PubChem 下载现在支持到什么程度？

V0.2.5 到 V0.2.10 已支持基础 raw 下载、raw 记录管理、来源查询增强、流程引导和 smoke test 整理。V0.3.0 额外新增自动准备状态模型和入口，但还不执行真实 PDBQT 准备：

- RCSB PDB：通过 4 位 PDB ID 下载受体原始结构，保存到 `raw/receptor_{PDB_ID}.pdb` 或 `.cif`；
- PubChem：通过 CID 下载配体原始 SDF，保存到 `raw/ligand_{cid}.sdf`。
- PubChem：通过名称下载配体原始 SDF，保存到 `raw/ligand_name_{name}.sdf`。
- SMILES 查询当前只返回“暂未支持”的中文结构化提示。
- StructureFetchPage 会显示 raw 文件是否存在、文件大小、修改时间、绝对路径和记录一致性。
- 可以清除 receptor/ligand 的 raw 记录。

这些 raw 文件不能直接运行 Vina。DockStart 仍然需要 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`。

V0.3.0 仍不会自动转 PDBQT，不会调用 RDKit/Meeko 做真实分子处理，也不会生成 3D 构象。SMILES 查询暂未支持的原因也是为了避免在当前阶段引入 RDKit 解析和分子处理。

## 6. 为什么下载了 raw 文件还不能运行 Vina？

AutoDock Vina 需要 PDBQT 输入。PDB、CIF、SDF 是原始结构或分子文件，仍需要准备步骤，例如加氢、处理电荷、设置可旋转键、写出 PDBQT。V0.2 raw workflow 负责下载、记录来源、显示 raw 状态、清除 raw 记录、基础来源查询、流程引导、手动准备文档和 smoke test 整理；V0.3.0 增加准备状态入口，但仍不做这些化学处理。

## 6.1 V0.3.0 的自动准备入口能直接生成 PDBQT 吗？

不能。V0.3.0 只建立 `project.json` 中的 `preparation` 数据模型、准备状态读取、前置检查和状态重置入口。真实 ligand/receptor PDBQT 自动生成会在后续阶段单独实现，并且仍需要用户检查科学合理性。

V0.2.8 开始在首页、创建页、下载页、导入页和工具链状态页明确提示当前流程：

```text
下载 raw 原始结构 → 手动准备 PDBQT → 导入 prepared PDBQT → 继续设置参数和运行 Vina
```

更详细说明见 [manual_pdbqt_preparation.md](manual_pdbqt_preparation.md)。

## 7. 清除 raw 记录会删除 prepared PDBQT 吗？

不会。

清除 raw 记录只会清空 `source`、`source_id`、`query_type`、`downloaded_at` 和 `raw_file`。它不会清空 `receptor.file` / `ligand.file`，也不会删除 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`。

如果你选择“同时删除 raw 文件”，DockStart 也只允许删除项目 `raw/` 目录下的文件，避免误删 prepared 输入文件。

## 8. 为什么没有 3D 可视化？

3D 可视化和可视化 box 设置很有价值，但它需要额外前端依赖、渲染状态管理和更完整的交互设计。V0.1 先完成手动 box 参数输入和可复现文件输出。

3Dmol.js / Mol* 可视化计划放在 V0.4 或之后。V0.3 目前优先作为 raw → prepared PDBQT 自动准备的设计、测试和许可证审查阶段。

## 9. 可以商用吗？

需要分别看 DockStart 本体许可证和第三方工具许可证。

DockStart 本体计划采用 Apache License 2.0；正式授权以仓库 `LICENSE` 文件为准。AutoDock Vina、React、Vite、Tauri、RDKit、Open Babel、PLIP、MGLTools 等都有各自许可证和分发要求。商用前请查看 [license_notes.md](license_notes.md)，并自行确认你的使用方式是否满足所有第三方许可证。

## 10. 可以把 Open Babel/MGLTools 打包进 DockStart 吗？

当前不打包。

Open Babel、MGLTools、PLIP 等工具涉及不同许可证和分发边界。没有确认许可证兼容和分发方式前，不应直接复制第三方源码或二进制进 DockStart。未来如果支持，也应优先作为外部可选工具，由用户自行安装，再通过 adapter 检测和调用。

## 11. DockStart 会保证外部工具生成的 PDBQT 科学正确吗？

当前不会。

DockStart 目前只做基础文件导入和运行流程管理，不判断受体链选择、质子化状态、电荷、可旋转键或 docking box 是否科学合理。外部工具生成的 PDBQT 仍需要用户按课程、实验室规范或研究方案自行确认。
