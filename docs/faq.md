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

Meeko/RDKit 自动处理计划放在 V0.2 或之后。

## 3. 为什么现在只支持 PDBQT？

AutoDock Vina 的核心输入是 PDBQT。V0.1 的目标是先跑通最小闭环，所以只接受用户已经准备好的 `receptor.pdbqt` 和 `ligand.pdbqt`。

PDB、SDF、MOL2 到 PDBQT 的自动转换需要额外工具和更复杂的化学处理逻辑，会在后续版本中谨慎加入。

## 4. docking score 越低是否代表药效越好？

不能这样解释。

Docking score 只表示在特定输入结构、box、参数和 Vina 版本下的计算结果。它可以作为结构结合趋势参考，但不能证明真实结合能力、药效、安全性或临床价值。任何药效判断都需要实验验证和更完整的研究流程。

DockStart 不会自动判断“药效好/不好”，也不会输出“候选药物”结论。

## 5. 为什么没有 PDB/PubChem 下载？

V0.1 优先保证本地 PDBQT docking 流程稳定。PDB/PubChem 下载会引入网络、数据格式、结构选择、配体状态和许可证边界等额外问题。

该能力计划放在 V0.2 或之后。

## 6. 为什么没有 3D 可视化？

3D 可视化和可视化 box 设置很有价值，但它需要额外前端依赖、渲染状态管理和更完整的交互设计。V0.1 先完成手动 box 参数输入和可复现文件输出。

3Dmol.js / Mol* 可视化计划放在 V0.3 或之后。

## 7. 可以商用吗？

需要分别看 DockStart 本体许可证和第三方工具许可证。

DockStart 本体计划采用 Apache License 2.0；正式授权以仓库 `LICENSE` 文件为准。AutoDock Vina、React、Vite、Tauri、RDKit、Open Babel、PLIP、MGLTools 等都有各自许可证和分发要求。商用前请查看 [license_notes.md](license_notes.md)，并自行确认你的使用方式是否满足所有第三方许可证。

## 8. 可以把 Open Babel/MGLTools 打包进 DockStart 吗？

当前不打包。

Open Babel、MGLTools、PLIP 等工具涉及不同许可证和分发边界。没有确认许可证兼容和分发方式前，不应直接复制第三方源码或二进制进 DockStart。未来如果支持，也应优先作为外部可选工具，由用户自行安装，再通过 adapter 检测和调用。
