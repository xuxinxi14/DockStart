# DockStart Toolchain Runtime

本文档说明 DockStart Full 工具链中 Python runtime 的当前设计和边界。当前版本已经具备 Python runtime 解析、manifest 检查、状态展示，以及在用户已有 RDKit/Meeko 环境可用时尝试 V0.3 ligand/receptor PDBQT 自动准备的后端能力。

## 为什么需要 Python runtime

DockStart 的后端以 Python 为主，后续如果要提供更接近开箱即用的 Full 版本，需要减少用户手动安装 Python、配置 PATH、安装包和处理版本兼容的负担。

内置 Python runtime 的目标是：

- 让 DockStart 能在受控环境中运行后端检测和后续工具；
- 记录 Python 版本、来源和 `sha256`，提高可复现性；
- 为后续离线 RDKit/Meeko 包管理预留基础；
- 避免把用户系统 Python 环境和 DockStart 项目状态混在一起。

## Python 来源

当前 Python 解析优先级为：

```text
bundled > configured > current_environment
```

含义：

- `bundled`：DockStart Full 资源目录中的 `resources/python/python.exe`。
- `configured`：用户在设置页中配置的 Python 路径。
- `current_environment`：当前运行 DockStart 后端的 Python 环境。

Meeko/RDKit 的 import、能力检测和 V0.3 自动准备都会使用解析后的 Python。也就是说，如果 bundled Python 存在，Meeko/RDKit 检测与准备会优先使用 bundled Python；如果用户在设置页配置了 Python，则在没有 bundled Python 时使用 configured Python；否则使用 current_environment。

## 推荐的 RDKit/Meeko conda 环境

V0.3.9 真实工具链验收推荐使用独立 conda/mamba 环境，不建议直接使用 Microsoft Store Python 3.13 作为 DockStart 工具链。原因是 RDKit/Meeko 对 Python 版本、二进制依赖和脚本入口更敏感，独立环境更容易复现和排错。

推荐环境名：

```text
dockstart-rdkit-meeko
```

推荐创建命令：

```powershell
conda create -n dockstart-rdkit-meeko -c conda-forge --override-channels python=3.11 rdkit meeko numpy scipy -y
```

如果使用 mamba，可将 `conda create` 替换为 `mamba create`。创建后在 DockStart 设置页中把 Python 路径配置为该环境的 `python.exe`，例如：

```text
C:\Users\<USER>\Miniconda3\envs\dockstart-rdkit-meeko\python.exe
```

V0.3.9 验收中，Meeko `0.7.1` 的 `mk_prepare_receptor.py` 仍依赖 `pkg_resources`。如果 receptor preparation 报错 `No module named 'pkg_resources'`，可以在该独立环境中安装兼容的 setuptools：

```powershell
conda install -n dockstart-rdkit-meeko -c conda-forge --override-channels "setuptools<81" -y
```

这只是本地工具链环境兼容处理，不应把环境目录、`python.exe`、`Lib/`、`DLLs/` 或 `site-packages/` 提交到 Git。

## bundled Python 目录结构

预期目录：

```text
resources/python/
├─ python.exe
├─ python*.dll
├─ DLLs/
├─ Lib/
├─ Scripts/
└─ README.md
```

当前仓库只提交：

```text
resources/python/README.md
```

真实 runtime 文件被 `.gitignore` 忽略，包括：

- `resources/python/python.exe`
- `resources/python/Lib/`
- `resources/python/DLLs/`
- `resources/python/Scripts/`
- `resources/python/site-packages/`

## bundled Vina 目录结构

V0.6 推荐的 bundled AutoDock Vina 路径为：

```text
resources/vina/vina.exe
```

旧版实验路径仍可作为兼容回退：

```text
resources/tools/vina/vina.exe
```

解析优先级为：

```text
resources/vina/vina.exe > resources/tools/vina/vina.exe > 用户配置路径 > PATH
```

仓库默认不提交真实 `vina.exe` 或 DLL 文件。准备 bundled Vina 时必须使用本地文件，记录版本、来源和 `sha256`，并确认 AutoDock Vina license 和 `THIRD_PARTY_NOTICES.md`。

## toolchain_manifest.json 的作用

`resources/toolchain_manifest.json` 记录工具链资源的可追踪信息。对 bundled Python，当前字段包括：

```json
{
  "bundled_python": {
    "name": "Python",
    "version": "",
    "binary_path": "resources/python/python.exe",
    "license": "Python Software Foundation License",
    "source": "",
    "bundled": false,
    "sha256": "",
    "prepared_at": ""
  }
}
```

这些字段用于 ToolchainStatusPage 展示和完整性检查。它们不代表仓库已经提交真实 runtime。

## prepare_bundled_python.py 用法

从本地 Python 目录装配：

```powershell
python scripts/prepare_bundled_python.py C:\Path\To\Python --source-label "local-python-3.11"
```

或直接传入 `python.exe`：

```powershell
python scripts/prepare_bundled_python.py C:\Path\To\Python\python.exe
```

脚本会：

- 复制本地 Python runtime 或 `python.exe` 到 `resources/python/`；
- 计算 `python.exe` 的 `sha256`；
- 尝试运行 `python.exe --version` 获取版本；
- 更新 `resources/toolchain_manifest.json` 的 `bundled_python` 字段。

脚本不会：

- 联网；
- 下载 Python；
- 安装 Python 包；
- 安装 RDKit；
- 安装 Meeko；
- 生成 PDBQT；
- 修改 docking 主流程。

## 为什么不提交 python.exe

当前默认不提交完整 Python runtime，原因包括：

- runtime 体积较大，不适合作为普通源码提交；
- 不同平台需要不同 runtime；
- Python、RDKit、Meeko 及其依赖需要单独维护版本和更新机制；
- 分发前需要确认许可证文本、来源、构建方式和安全更新策略；
- `site-packages/` 很容易混入不必要或许可证不清晰的包。

## Meeko/RDKit 当前状态

V0.3.1 开始，Meeko/RDKit 不再只显示 import 是否成功，还会做准备能力检测：

```text
python -c "import meeko"
python -c "import rdkit"
```

- RDKit：检测 import、版本，并用内联 SDF 样本探测基础 SDF 读取能力，不写项目目录；
- Meeko：检测 import、版本，并通过安全 introspection / CLI 发现判断 ligand/receptor preparation 能力是否可确认；
- 如果能力不可确认，返回 `unknown`，不把未知能力硬写成成功。

V0.3.2 开始，DockStart 可以使用 RDKit + Meeko 尝试把 ligand SDF/MOL raw 文件准备为 `prepared/ligand.pdbqt`。V0.3.3 开始，DockStart 可以使用 Meeko receptor CLI 尝试把 receptor PDB/CIF raw 文件准备为 `prepared/receptor.pdbqt`。当前仍不支持 MOL2/SMILES 自动准备，也不会把这些结果解释为药效判断。

V0.3.8 的真实工具链验收确认了一个重要边界：如果当前解析到的 Python 缺少 RDKit 或 Meeko，DockStart 会返回 `missing` 和中文提示，不会自动安装依赖，也不会假装生成 PDBQT。用户需要在设置页配置一个已经安装 RDKit/Meeko 的 Python 环境，或自行准备 PDBQT 后走手动导入流程。

V0.3.9 进一步确认：当 DockStart 配置到独立 `dockstart-rdkit-meeko` conda 环境后，RDKit/Meeko 可以被检测为 `ok`，并可在临时项目中真实生成 ligand/receptor PDBQT。该结果只说明工具链调用闭环可用，不代表自动准备结果在科学上一定正确。

## 许可证和依赖注意事项

- Python runtime 需要保留 Python Software Foundation License 相关说明；
- RDKit 使用 BSD 系列许可证，但随包依赖仍需确认；
- Meeko 的许可证和依赖合规需要单独检查；
- Open Babel、PLIP、MGLTools 当前不进入核心内置包；
- 不应把第三方源码或大体积二进制直接复制进仓库，除非许可证和分发边界已经明确。

## 后续要求

如果后续真正内置 RDKit/Meeko，需要单独审查：

- 许可证文本和第三方 notices；
- 源码获取方式；
- 修改说明；
- 包体积；
- 更新机制；
- Python 版本兼容性；
- 离线安装方式；
- 是否允许随 DockStart Full 打包分发。

在这些问题明确前，DockStart 不提交完整 Python runtime 或 `site-packages/`。V0.3 自动准备只使用用户已配置或当前环境中已经可用的 RDKit/Meeko，不负责自动安装、升级或科学判断准备结果。
