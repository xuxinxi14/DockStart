# Windows Packaging

本文档定义 DockStart v0.11.2 Windows x86_64 的可重复发布入口。构建脚本只能在干净的
`main` 分支运行，并从白名单 stage 生成 MSI 与 NSIS；禁止直接把开发目录中的
`resources/python` 或旧 `target/release` 内容复制进安装包。

## Profile 选择

Basic Stable：已有 receptor/ligand PDBQT 的最小依赖闭环。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -Profile Basic
```

Assisted Stable：额外包含固定、离线、可替换的 RDKit/Meeko 工具链。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -Profile Assisted
```

两个 profile 都随附 Vina、DockStart 后端、许可证和 `resources/examples/`。Basic 的
Python 仅运行后端且排除 `site-packages`；Assisted 的独立 runtime 包含固定 scientific
packages，但不包含 conda、ProDy、Open Babel、PLIP 或 MGLTools。

## 可重复资源 stage

Basic：

```powershell
python scripts/prepare_basic_release_resources.py --repo-root .
```

输出 `.release/basic/`，必须无 `site-packages`、`Scripts` 与 Python bytecode。

Assisted：

```powershell
python scripts/prepare_assisted_release_resources.py --repo-root .
```

输出 `.release/assisted/`。脚本只读取固定 CPython base runtime 与
`_external_download/assisted-wheelhouse/`，逐个核对 `SOURCE_MANIFEST.json` 的 SHA256，
不会联网或调用 pip 解析依赖。维护者只有在有意刷新上游 artifact 时才执行：

```powershell
python scripts/fetch_assisted_sources.py --repo-root .
```

wheel、source archive、runtime、stage 与 installer 都被 Git 忽略。

## 构建顺序

发布入口依次执行：

1. 检查 `main`、干净工作树和七处版本一致；
2. 从空目录生成对应白名单 stage，校验 Vina/Python/package/license SHA256；
3. 运行 Python 全量测试、前端生产构建、Cargo check/test；
4. 清理 Tauri release 目录内已验证的旧资源和 bundle；
5. 用对应 `tauri.basic.conf.json` 或 `tauri.assisted.conf.json` 生成 MSI/NSIS；
6. 对打包后的 `target/release` 执行真实流程回归；
7. Assisted 额外真实静默安装 NSIS、从安装目录回归、静默卸载并检查残留；
8. 只接受当前版本的 MSI/NSIS，生成带大小、SHA256 和门禁状态的 manifest。

`-SkipTauriBuild` 只用于验证打包前门禁，不产生可发布证据。Assisted 的
`-SkipPostInstallGate` 只生成开发产物，并强制写入 `publishable=false`。

## Assisted 三道强制门禁

- `development`：在 `.release/assisted/` 中，用包含中文与空格的项目路径完成 PDB 受体、
  SDF 配体准备、Vina 运行、结果解析和报告；禁用网络代理，并验证 configured 优先与 bundled fallback；
- `post-package`：在 Tauri `target/release/` 资源布局重复完整流程；
- `post-install`：把 NSIS 安装到 `.release/install-gate/installed/`，从真实安装目录重复流程，
  然后卸载并确认目录、runtime 与卸载记录均无残留。

安装门禁发现已有 DockStart 安装、运行进程、默认目录或非空隔离目录时必须拒绝运行，
不能覆盖用户安装。其删除操作只能发生在已校验的 `.release/install-gate/` 内路径。

## 产物

```text
.release/artifacts/<version>/<basic|assisted>/DockStart_<version>_<Basic|Assisted>_x64_en-US.msi
.release/artifacts/<version>/<basic|assisted>/DockStart_<version>_<Basic|Assisted>_x64-setup.exe
.release/<profile>/artifact-manifest.json
```

Tauri 原始同名产物在门禁内立即重命名为带 profile 的文件名，避免先后构建时覆盖或让用户
无法判断安装包能力；随后复制到 profile 隔离的 `.release/artifacts/`，因此下一次构建清理
`target/release/bundle` 不会删除上一 profile 的候选产物。两个 profile 仍使用同一应用身份，
不应并行安装。

Assisted 的 manifest 只有在三道门禁均通过时才可写 `publishable=true`。最终发布报告必须
记录精确路径、大小、SHA256、运行命令和门禁结果。

## 安装状态之外的验证

自动安装门禁覆盖 NSIS 的实际资源布局与卸载安全，但不能替代以下人工/独立验证：

- 从真实桌面 GUI 完成一次 Assisted 流程并重启重开项目；
- 对 MSI 做独立全新安装/卸载烟雾测试；
- 在无开发版 Python、无 PATH Vina、断网的干净 Windows 上复验；
- 从上一稳定版升级，确认没有旧 scientific runtime 残留；
- 确认卸载不会删除用户项目目录。

能力边界以 `release_artifact_profile.md` 为准，许可证边界以 `docs/license_notes.md` 和
安装包内 `THIRD_PARTY_NOTICES.md` 为准。
