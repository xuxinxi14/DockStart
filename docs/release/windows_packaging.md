# Windows Packaging

本文档定义 DockStart v0.9.7 Basic Stable 的 Windows 发布门禁。目标是生成可重复、
能力边界明确的 MSI 与 NSIS 安装包；不是把本机 `resources/python` 的全部内容直接
复制进安装包。

## 发布档案

Basic Stable 必须满足：

- 随包提供 AutoDock Vina；
- 随包提供仅用于 DockStart 后端的精简 Python runtime；
- 不随包提供 `Lib/site-packages`、`Scripts`、RDKit 或 Meeko；
- 运行时示例唯一来源为 `resources/examples/`；
- 用户提供 receptor/ligand PDBQT 后，可以离线完成真实 Vina 对接、解析和报告导出；
- Assisted Mode 保留，但需要用户配置独立 RDKit/Meeko Python 环境。

## 可重复资源暂存

源码目录中的真实 Python、Vina 与安装包都由 `.gitignore` 排除。发布时先执行：

```powershell
python scripts/prepare_basic_release_resources.py --repo-root .
```

脚本会删除并重新生成 `.release/basic/`，只暂存：

```text
.release/basic/
├─ backend/                 # 仅 Python 源码，无 __pycache__/pyc
├─ frontend/package.json
└─ resources/
   ├─ examples/
   ├─ licenses/
   ├─ python/               # 标准库 + DLL，无 site-packages/Scripts
   ├─ vina/
   └─ toolchain_manifest.json
```

`apps/desktop/src-tauri/tauri.conf.json` 不直接复制仓库原始 `resources/`。
`tauri.basic.conf.json` 只把上述白名单 stage 映射进安装包，避免本地 Full 候选文件或
旧 `target/release` 内容泄漏到 Basic 包。

## 权威构建命令

在 `main` 分支、干净工作树中运行：

```powershell
scripts/build_windows_release.ps1 -Profile Basic
```

脚本按顺序执行：

1. 检查分支、干净工作树和七处版本号；
2. 从空目录生成 Basic stage；
3. 硬断言 Vina/Python/许可证/运行时示例存在；
4. 硬断言 RDKit、Meeko、`site-packages`、`Scripts` 和 Python bytecode 不存在；
5. 校验 Vina/Python SHA256；
6. 执行后端 unittest、前端构建和 Cargo check；
7. 安全清理旧的 Tauri release 资源与 bundle；
8. 使用 Basic Tauri 配置构建 MSI 与 NSIS；
9. 对 `target/release` 执行真实 Basic Demo 两次运行回归；
10. 只接受当前版本的两个安装包并生成 SHA256 artifact manifest。

只验证构建前门禁时可运行：

```powershell
scripts/build_windows_release.ps1 -Profile Basic -SkipTauriBuild
```

开发者手动构建 Basic 桌面包时使用：

```powershell
Push-Location apps/desktop
npm run build:desktop -- --bundles msi,nsis --ci
Pop-Location
```

不要直接使用未带 Basic 配置的 `tauri build` 作为发布产物。

## 产物与验证

期望文件：

```text
apps/desktop/src-tauri/target/release/bundle/msi/DockStart_<version>_x64_en-US.msi
apps/desktop/src-tauri/target/release/bundle/nsis/DockStart_<version>_x64-setup.exe
.release/basic/artifact-manifest.json
```

post-package 回归可以单独重跑：

```powershell
python scripts/verify_basic_release.py apps/desktop/src-tauri/target/release
```

该回归使用发布目录中的 Python、Vina、后端和 `resources/examples/basic_pdbqt`，在
包含中文和空格的临时路径中完成：示例创建、校验、配置生成、run 准备、真实 Vina
执行、结果解析、pose 读取、报告导出、项目重开和第二次运行。它同时检查运行命令、
输入 SHA256、时间戳、stdout/stderr/log、scores、报告与科学免责声明。

## 安装态验收

`target/release` 回归是必须门禁，但不能替代干净 Windows 安装验证。公开发布前还应
分别验证 NSIS 与 MSI：

- 普通用户权限下全新安装；
- 机器没有开发版 Python、PATH Vina，且断网；
- 安装目录再次运行 `verify_basic_release.py`；
- GUI 完成“打开 Basic 示例 → 运行 → 结果 → 报告 → 重启再打开”；
- 从上一稳定版升级后不存在旧 Meeko/RDKit 残留；
- 卸载后用户项目目录不被删除。

## 不得提交到 Git

- `.release/`；
- `apps/desktop/dist/`；
- `apps/desktop/src-tauri/target/`；
- `.msi`、安装器 `.exe`；
- `resources/vina/vina.exe` 与 DLL；
- `resources/python/python.exe`、`Lib/`、`DLLs/`、`Scripts/`、`site-packages/`；
- 用户设置、真实 docking 输出和大型 raw 下载文件。

安装包能力说明以 `docs/release/release_artifact_profile.md` 为准，许可证边界以
`docs/license_notes.md` 和随包 `THIRD_PARTY_NOTICES.md` 为准。
