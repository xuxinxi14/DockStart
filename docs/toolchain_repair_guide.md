# DockStart 工具链修复指南

DockStart V0.8.4 开始提供结构化“修复建议”。这些建议只说明问题、影响的使用模式和推荐步骤，不会自动安装工具、不会修改系统 PATH，也不会联网下载大型环境。

## 影响范围

- Vina 缺失：Basic Mode 和 Assisted Mode 都无法真实运行 docking。
- RDKit / Meeko 缺失：只影响 Assisted Mode，也就是 raw PDB/SDF 自动准备 PDBQT。
- 示例资源缺失：只影响 Demo Mode。

如果你已经有 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`，RDKit/Meeko 缺失不应阻止 Basic Mode。

## 修复 AutoDock Vina

建议步骤：

1. 确认本机已有 AutoDock Vina。
2. 在终端运行：

```powershell
vina --version
```

3. 如果终端找不到 Vina，请在 DockStart 设置页填写 `vina.exe` 的完整路径。
4. 回到工具链页点击“重新检测”。

如果你在准备 toolchain-assisted release，可以使用 `scripts/prepare_bundled_vina.py` 从本地 `vina.exe` 装配 bundled Vina。该脚本不联网、不自动下载 Vina。

## 修复 Python + RDKit + Meeko

推荐使用独立 conda/mamba 环境，不建议把 RDKit/Meeko 安装进 Microsoft Store Python 或系统 Python。

推荐命令：

```powershell
conda create -n dockstart-rdkit-meeko -c conda-forge python=3.11 rdkit meeko numpy scipy
conda run -n dockstart-rdkit-meeko python -c "import rdkit, meeko; print('RDKit/Meeko ok')"
```

如果 conda-forge 上 Meeko 解析失败，可以先安装 `python=3.11 rdkit numpy scipy`，再在该环境中手动安装 Meeko。执行前请确认包来源和许可证。

配置方法：

1. 找到该环境的 `python.exe`。
2. 在 DockStart 设置页填写 Python 路径。
3. 回到工具链页点击“重新检测”。

## Microsoft Store Python 提示

如果 Python 路径位于 `WindowsApps` 或包含 `PythonSoftwareFoundation`，DockStart 会提示它不适合作为 RDKit/Meeko 工具链。原因是该环境的包管理和路径行为容易不稳定。

建议改用独立 conda/mamba 环境，并在 DockStart 中配置该环境的 `python.exe`。

## 科学边界

工具链修复只解决“软件能否运行对应流程”的问题。即使 RDKit/Meeko 可用，自动生成的 PDBQT 也仍需要用户检查质子化、电荷、构象、缺失残基、水、金属和辅因子等科学合理性。
