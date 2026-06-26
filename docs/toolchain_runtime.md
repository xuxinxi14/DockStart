# DockStart Toolchain Runtime

本文档说明 DockStart Full 工具链中 Python runtime 的当前设计和边界。当前版本只实现 runtime 解析、manifest 检查和状态展示，不实现 RDKit/Meeko 分子处理。

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

Meeko/RDKit 的 import 检测会使用解析后的 Python。也就是说，如果 bundled Python 存在，Meeko/RDKit 检测会优先使用 bundled Python。

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

在这些问题明确前，DockStart 只保持检测和状态展示，不做 RDKit/Meeko 分子处理。
