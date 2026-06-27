# RDKit / Meeko Toolchain Environment

DockStart V0.6 不自动安装 RDKit 或 Meeko，也不提交 conda 环境。本页说明推荐的可复现工具链环境。

## Recommended Environment

推荐使用独立 conda/mamba 环境：

```text
dockstart-rdkit-meeko
```

推荐 Python 版本：

```text
3.11
```

推荐安装源：

```text
conda-forge
```

推荐包：

```text
python=3.11
rdkit
meeko
numpy
scipy
```

创建示例：

```powershell
conda create -n dockstart-rdkit-meeko -c conda-forge --override-channels python=3.11 rdkit meeko numpy scipy -y
```

如果使用 mamba，可把 `conda create` 换成 `mamba create`。

## Why Not Microsoft Store Python

不建议使用 Microsoft Store Python 作为 DockStart 工具链环境。RDKit 和 Meeko 依赖二进制包、脚本入口和 Python 版本兼容，独立 conda/mamba 环境更容易复现，也更容易排错。

## Configure DockStart

创建环境后，在 DockStart 设置页把 Python 路径配置为该环境中的 `python.exe`，例如：

```text
C:\Users\<USER>\Miniconda3\envs\dockstart-rdkit-meeko\python.exe
```

保存后回到工具链页或 PreparationPage 重新检测。理想状态：

- Python 来源：configured；
- RDKit：ok；
- Meeko：ok；
- ligand/receptor preparation capability：ok 或明确的 structured 状态。

## Export Environment YML

V0.6.2 新增脚本：

```powershell
python scripts/export_toolchain_environment.py
```

脚本会读取 DockStart 设置中的 configured Python。如果它属于 conda 环境，会生成：

```text
docs/release/environment-dockstart-rdkit-meeko.yml
```

脚本只读取环境信息并写出 yml，不会：

- 联网；
- 安装包；
- 修改系统 PATH；
- 复制 conda env；
- 提交 `site-packages/`；
- 生成 PDBQT；
- 修改 docking 主流程。

可使用 dry-run：

```powershell
python scripts/export_toolchain_environment.py --dry-run
```

## Meeko setuptools Compatibility

部分 Meeko receptor CLI 版本仍依赖 `pkg_resources`。如果出现：

```text
No module named 'pkg_resources'
```

可在独立环境中安装兼容 setuptools：

```powershell
conda install -n dockstart-rdkit-meeko -c conda-forge --override-channels "setuptools<81" -y
```

这只是用户本地环境兼容处理，不应把环境目录提交到 Git。

## Scientific Boundary

RDKit/Meeko 环境可用只说明 DockStart 能调用工具链。自动生成的 PDBQT 仍需要用户检查质子化、电荷、构象、缺失残基、水、金属、辅因子和链选择等问题。它不代表真实结合、药效、安全性或临床价值。

