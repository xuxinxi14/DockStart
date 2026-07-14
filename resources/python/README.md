# DockStart Basic Backend Python Runtime

`resources/python/` 是 DockStart 本地构建输入目录。真实 `python.exe`、`Lib/`、
`DLLs/`、`Scripts/` 与 `site-packages/` 均由 `.gitignore` 排除，不随源码仓库提交。

DockStart v0.10.2 Basic Stable 的安装包只分发运行 Python 后端所需的精简 CPython：

- 包含 `python.exe`、标准库和运行所需 DLL；
- 不包含 `Lib/site-packages/`；
- 不包含 `Scripts/`；
- 不包含 `Lib/ensurepip` 及其内嵌的 pip/setuptools wheel；
- 不包含 RDKit、Meeko、NumPy、SciPy、ProDy 或其命令行工具。

发布时必须通过：

```powershell
python scripts/prepare_basic_release_resources.py --repo-root .
```

该命令会从本地构建输入生成全新的 `.release/basic/` 白名单资源树，过滤
`site-packages`、`Scripts`、`__pycache__` 与字节码，并执行隔离的标准库探针。
它不联网、不安装 Python 包，也不修改用户配置。

桌面后端的 Python 解析优先级为：

```text
bundled > configured > current_environment
```

Assisted Mode 的准备工具链解析优先级为：

```text
configured > bundled > current_environment
```

因此 v0.10.2 Basic Stable 中，如果用户需要从 PDB/SDF 自动准备 PDBQT，仍需在
设置页配置包含 RDKit 与 Meeko 的独立 Python 环境。缺少这些包不会阻止已有
PDBQT 的 Basic Mode。

v0.10.2 Assisted Stable 使用单独的 `.release/assisted/` 白名单 stage，从固定离线
wheelhouse 装配普通目录形式、可替换的 RDKit/Meeko 工具链；它不会直接复制此目录中
可能存在的可变 `site-packages`。
