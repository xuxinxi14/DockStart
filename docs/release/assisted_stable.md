# DockStart v0.10.0 Assisted Stable 发布门禁

Assisted Stable 是第二阶段能力，不修改 AutoDock Vina 算法。它只把现有
PDB/SDF/MOL → PDBQT 最小准备流程放入可复现、离线、可替换的 Python 工具链。

## 发布边界

- Windows x86_64；CPython 3.11.15 / `cp311` ABI；
- AutoDock Vina 1.2.7；
- Meeko 0.7.1、RDKit 2026.3.3、NumPy 1.26.4、SciPy 1.17.1、Gemmi 0.7.5、
  Pillow 12.2.0、tqdm 4.67.1、tomli 2.2.1、colorama 0.4.6；
- 不包含 ProDy、Biopython、pyparsing、Open Babel、PLIP、MGLTools 或 conda；
- 不包含 `ensurepip` 及其内嵌的 pip/setuptools wheel；发布 runtime 不提供包安装器；
- preparation Python 解析顺序：用户配置 → bundled → 当前开发环境；
- Meeko 是普通 `Lib/site-packages/meeko/` 目录，可由用户替换；hash 只作 provenance、
  缓存键和告警，不得阻止替换；
- 自动准备不保证质子化、电荷、缺失残基、水、金属、辅因子或构象正确。

## 一次性准备离线 wheelhouse

联网下载只能由维护者显式执行：

```powershell
python scripts/fetch_assisted_sources.py --repo-root .
```

脚本只接受 `resources/assisted/SOURCE_MANIFEST.json` 中的
`files.pythonhosted.org` 固定 URL，并在落盘前核对 SHA256。下载目录
`_external_download/assisted-wheelhouse/` 被 Git 忽略。不要提交 wheel、sdist、
runtime 或 installer。

## 离线装配

断网后执行：

```powershell
python scripts/prepare_assisted_release_resources.py --repo-root .
```

该命令从固定 CPython 3.11 base runtime 构造全新的 `.release/assisted/`，不会复制
源 runtime 中可变的 `site-packages`，也不会调用 pip 解析依赖。每个 wheel 在解包前
必须匹配文件名和 SHA256。stage 完成后会验证：

- 精确 distribution 版本；
- RDKit/Meeko/NumPy/SciPy/Gemmi/Pillow/tqdm/tomli/colorama import；
- `python -I -B -m meeko.cli.mk_prepare_ligand --help`；
- `python -I -B -m meeko.cli.mk_prepare_receptor --help`；
- 无 `.pyc`、`.pyo`、`__pycache__`；
- Python runtime tree 和每个 distribution RECORD 的有序聚合 hash；
- Meeko、Gemmi、tqdm 同版本 source archive 和许可证。

## 三道强制门禁

完整构建入口：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -Profile Assisted
```

发布脚本必须按顺序通过：

1. `development`：在 Tauri 打包前，对 `.release/assisted/` 执行真实 PDB receptor +
   SDF ligand 准备、Vina docking、结果解析与报告导出；项目路径含中文和空格，代理指向
   不可用本地端口，证明流程不依赖网络；同时验证用户配置 Python 优先和 bundled fallback。
2. `post-package`：Tauri 复制资源后，在 `target/release/` 上重复同一完整回归，避免
   “开发目录可用、安装资源缺文件”。
3. `post-install`：生成 MSI/NSIS 后，默认把 NSIS 产物以静默方式真实安装到仓库专用的
   `.release/install-gate/installed/`，从该实际安装目录执行
   `verify_assisted_release.py --gate post-install`，然后静默卸载；安装目录、bundled Python
   runtime 和 NSIS 卸载注册记录均不得残留。通过结果写入
   `.release/assisted/artifact-manifest.json`。

安装门禁会先检查正在运行的 DockStart、卸载注册表记录、默认安装目录和隔离目录。
只要发现已有 DockStart 安装或 `.release/install-gate/installed/` 非空，就会拒绝继续，
不会覆盖用户的现有安装。失败诊断保留在 `.release/install-gate/diagnostics/`；清理逻辑
只允许操作 `.release/install-gate/` 内已经校验过的路径。

只有三道结果均为 `passed` 且 artifact manifest 中 `publishable` 为 `true`，才可以发布
Assisted Stable。`-SkipTauriBuild` 只用于本地开发，会跳过打包后门禁，不能作为发布证据。
若只需要生成开发用安装包，可直接调用 Assisted 构建脚本并显式跳过真实安装：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_assisted_release.ps1 -SkipPostInstallGate
```

此开关会把 `post_install_gate` 记为 `pending`、`publishable` 记为 `false`，脚本也会明确
提示“不可发布”。默认构建不接受隐式跳过。发布门禁还会执行 `cargo test`，不再只执行
`cargo check`。

## 许可证交付

安装资源必须包含：

- `resources/licenses/THIRD_PARTY_NOTICES.md`；
- `resources/licenses/python-packages/` 下的 wheel 原始许可证；
- `resources/sources/SOURCE_MANIFEST.json`；
- `resources/sources/meeko-0.7.1.tar.gz`；
- `resources/sources/gemmi-0.7.5.tar.gz`；
- `resources/sources/tqdm-4.67.1.tar.gz`。

若修改 Meeko/Gemmi/tqdm、改变调用边界或冻结进单文件可执行程序，不得沿用本门禁结论，
必须重新进行许可证审查。
