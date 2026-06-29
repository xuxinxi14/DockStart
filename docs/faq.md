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
- 如果你正在准备 V0.6 toolchain-assisted release，可用 `scripts/prepare_bundled_vina.py` 从本地 Vina 文件装配到 `resources/vina/`，DockStart 会优先检测该路径；
- 保存设置后重新运行工具检测。

V0.6.3 之后，如果你还没有创建项目，Dashboard 会先显示首次启动工具链检查。它会提示是否应先配置 Vina 或 Python/RDKit/Meeko，再创建项目。

## 2. 为什么 Meeko/RDKit 显示 missing？

V0.1 只检测 Meeko/RDKit 是否存在，不使用它们自动处理分子结构。V0.3.1 之后会检测准备能力，V0.3.2/V0.3.3 可以在工具链可用时尝试生成 ligand/receptor PDBQT。显示 missing 不影响本地 PDBQT docking MVP，只要你已经准备好 `receptor.pdbqt` 和 `ligand.pdbqt`，并且 Vina 可用，就可以继续 V0.1 流程。

V0.8 中这条最低依赖路径被称为 **Basic Mode**。RDKit/Meeko 缺失只会影响 **Assisted Mode**，也就是从 raw PDB/SDF 自动准备 PDBQT 的增强路径，不代表 DockStart 整体不可用。

DockStart 不会自动安装 Meeko/RDKit，也不会保证自动准备结果科学正确；生成的 PDBQT 仍需要用户检查。

如果你想使用 V0.3 自动准备，请在设置页配置一个已经安装 RDKit 和 Meeko 的 Python 环境，然后回到工具链状态页或 PreparationPage 重新检测。V0.3.8 真实工具链验收中，当前环境缺少 RDKit/Meeko 时，DockStart 会返回 `missing` 和中文提示，不会自动安装，也不会假装准备成功。

V0.3.9 推荐使用独立 conda/mamba 环境，例如 `dockstart-rdkit-meeko`，安装 `python=3.11 rdkit meeko numpy scipy` 后，把该环境的 `python.exe` 配置到 DockStart。不要把 RDKit/Meeko 直接安装到 Microsoft Store Python 3.13 中。如果 Meeko receptor CLI 报 `No module named 'pkg_resources'`，通常需要在该独立环境中安装兼容的 `setuptools<81`。

V0.6.2 提供 `scripts/export_toolchain_environment.py`，用于把当前 configured conda Python 的版本信息导出为轻量 yml。该脚本不会联网、不会安装 RDKit/Meeko，也不会把 conda env 或 site-packages 提交进仓库。

## 2.1 Basic / Assisted / Demo Mode 有什么区别？

- Basic Mode：你已经有 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`，DockStart 只需要 AutoDock Vina 即可继续 docking。
- Assisted Mode：你只有 raw PDB/CIF/SDF/MOL，需要 Python + RDKit + Meeko 来尝试准备 PDBQT。
- Demo Mode：你想先用小型示例理解流程。示例只能说明软件怎么用，不能作为科研结论。

Dashboard 会给出当前可用模式、阻塞项和下一步建议。Vina 缺失会阻塞 Basic Mode；RDKit/Meeko 缺失只会阻塞 Assisted Mode。

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

V0.3.3 支持 ligand SDF/MOL raw 文件到 `prepared/ligand.pdbqt`，以及 receptor PDB/CIF raw 文件到 `prepared/receptor.pdbqt` 的最小自动准备。DockStart 当前仍不会处理 MOL2/SMILES，也不会生成 3D 构象。SMILES 查询暂未支持的原因也是为了避免在当前阶段引入更多 RDKit 解析和分子处理边界。

## 6. 为什么下载了 raw 文件还不能运行 Vina？

AutoDock Vina 需要 PDBQT 输入。PDB、CIF、SDF 是原始结构或分子文件，仍需要准备步骤，例如加氢、处理电荷、设置可旋转键、写出 PDBQT。V0.2 raw workflow 负责下载、记录来源、显示 raw 状态、清除 raw 记录、基础来源查询、流程引导、手动准备文档和 smoke test 整理；V0.3.2/V0.3.3 开始在工具链可用时尝试自动准备部分格式，V0.3.4 会在 config/run 前置检查里提示 raw 已有但 prepared PDBQT 还缺失的状态。

## 6.1 V0.3.0 的自动准备入口能直接生成 PDBQT 吗？

部分可以。V0.3.0 建立 `project.json` 中的 `preparation` 数据模型、准备状态读取、前置检查和状态重置入口。V0.3.1 进一步检测 RDKit/Meeko 的 import、版本和准备能力是否可确认。V0.3.2 可以在工具链可用时，把 ligand SDF/MOL raw 文件尝试生成 `prepared/ligand.pdbqt`。V0.3.3 可以在 Meeko receptor CLI 可用时，把 receptor PDB/CIF raw 文件尝试生成 `prepared/receptor.pdbqt`。

当前仍不支持 MOL2/SMILES 自动准备。生成出的 receptor/ligand PDBQT 仍需要用户检查质子化、电荷、构象、手性、缺失残基、金属离子、水分子、辅因子等科学合理性。

V0.2.8 开始在首页、创建页、下载页、导入页和工具链状态页明确提示当前流程：

```text
下载 raw 原始结构 → 手动准备 PDBQT → 导入 prepared PDBQT → 继续设置参数和运行 Vina
```

更详细说明见 [manual_pdbqt_preparation.md](manual_pdbqt_preparation.md)。

## 7. 清除 raw 记录会删除 prepared PDBQT 吗？

不会。

清除 raw 记录只会清空 `source`、`source_id`、`query_type`、`downloaded_at` 和 `raw_file`。它不会清空 `receptor.file` / `ligand.file`，也不会删除 `prepared/receptor.pdbqt` 或 `prepared/ligand.pdbqt`。

如果你选择“同时删除 raw 文件”，DockStart 也只允许删除项目 `raw/` 目录下的文件，避免误删 prepared 输入文件。

## 8. V0.4 Viewer 能做什么？

V0.4 已新增最小 ViewerPage。它可以查看项目内 raw/prepared 结构文件，显示和保存现有 Box 参数，并在 Vina 运行后按 mode 查看 `out.pdbqt` 中的 docking pose。

Viewer 只是几何查看和流程复核入口，不做 PLIP/ProLIF、相互作用分析、pocket prediction、药效判断，也不能替代 PyMOL、ChimeraX 等专业分子建模检查。

## 9. 可以商用吗？

需要分别看 DockStart 本体许可证和第三方工具许可证。

DockStart 本体计划采用 Apache License 2.0；正式授权以仓库 `LICENSE` 文件为准。AutoDock Vina、React、Vite、Tauri、RDKit、Open Babel、PLIP、MGLTools 等都有各自许可证和分发要求。商用前请查看 [license_notes.md](license_notes.md)，并自行确认你的使用方式是否满足所有第三方许可证。

## 10. 可以把 Open Babel/MGLTools 打包进 DockStart 吗？

当前不打包。

Open Babel、MGLTools、PLIP 等工具涉及不同许可证和分发边界。没有确认许可证兼容和分发方式前，不应直接复制第三方源码或二进制进 DockStart。未来如果支持，也应优先作为外部可选工具，由用户自行安装，再通过 adapter 检测和调用。

## 11. DockStart 会保证外部工具生成的 PDBQT 科学正确吗？

当前不会。

DockStart V0.3 可以尝试用 RDKit/Meeko 自动准备部分 receptor/ligand PDBQT，但不判断受体链选择、质子化状态、电荷、可旋转键、缺失残基、水分子、金属离子、辅因子或 docking box 是否科学合理。自动或外部工具生成的 PDBQT 都仍需要用户按课程、实验室规范或研究方案自行确认。

## 12. V0.4 之后下一步做什么？

V0.4 已完成基础 3D viewer、Box overlay 和 docking pose 查看。后续才考虑相互作用分析、批量 docking、更专业的结构检查和报告增强。当前仍不做 Open Babel、MGLTools、PLIP、分子动力学、PDF 报告或药效判断。

V0.5 已完成前端工作流整改：项目总览、侧边栏、workflow stepper、统一状态/错误展示、HelpPage 和关键页面信息层级整理。V0.5 不新增相互作用分析，不改变 Vina 算法，也不改变 RDKit/Meeko preparation 后端逻辑。

## V0.4.0 Viewer 后端已经能做什么？

V0.4.0 已完成 3D viewer 所需的后端结构文件读取接口，但该阶段还没有正式 3D 页面。V0.4.0 接口可以读取项目内 raw/prepared/out.pdbqt 文本结构文件，并能列出 docking pose 文本；V0.4.1 已接入最小 3Dmol.js ViewerPage。

Viewer 只负责几何查看的数据通道，不做相互作用分析、pocket prediction 或药效判断。读取文件时会拒绝项目目录外路径，并对超过 20 MB 的结构文件返回中文结构化提示。

V0.4.1 的 ViewerPage 使用 npm 依赖 `3dmol`，不使用外部 CDN。它能尝试显示 PDB/PDBQT/CIF/SDF/MOL/MOL2 文本结构文件；不同格式的显示效果取决于 3Dmol.js 对该格式的支持。显示成功也不代表结构准备或 docking 结果在科学上正确。

V0.4.2 可以在 ViewerPage 中显示并保存 docking box 参数。这个 Box overlay 只对应 Vina 搜索空间参数，不是 pocket prediction；如果 size 设得过大，DockStart 会显示 warning，但是否合理仍需要用户结合受体结构和研究目标判断。

V0.4.3 可以读取 Vina 输出的 `out.pdbqt` 并按 mode 查看 pose。如果 `scores.csv` 已由结果解析步骤生成，ViewerPage 会显示 affinity 和 RMSD 摘要；如果没有 `scores.csv`，仍可查看 pose，但不会显示分数摘要。DockStart 不会从 pose 自动推断氢键、疏水作用、盐桥或药效。

## Viewer 能替代 PyMOL、ChimeraX 或专业建模检查吗？

不能。DockStart V0.4 Viewer 是面向初学者 workflow 的最小几何查看入口，用于确认文件是否加载、Box 大致位置、pose mode 是否能查看。它不做结构修复、不做 pocket prediction、不解释相互作用、不判断结合是否真实，也不替代专业分子建模软件和实验验证。
