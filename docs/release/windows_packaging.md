# Windows Packaging

本文档记录 DockStart Windows 打包策略。V0.6 的重点是可重复打包和发布前检查，不是把第三方大体积 runtime 直接提交到仓库。

## Release Profiles

### Developer build

```powershell
cd E:\DockStart
python -m unittest discover -s backend/tests
cd apps\desktop
npm run build
cd E:\DockStart
cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml
```

### Lightweight release

轻量安装包包含 DockStart 本体、后端代码、前端资源、文档和 `resources/` 中的 README/manifest/license 文件。它不包含完整 Python runtime、conda env、RDKit/Meeko site-packages 或真实 docking 输出。

### Toolchain-assisted release

如果本地已经准备了 bundled Vina 或 bundled Python runtime，Tauri bundle 可以把 `resources/` 复制进安装包。打包前必须检查：

- `resources/toolchain_manifest.json`；
- `resources/licenses/THIRD_PARTY_NOTICES.md`；
- AutoDock Vina license 文件；
- bundled 二进制的 sha256；
- `.gitignore` 是否仍然排除大体积 runtime 和安装包产物。

V0.6 bundled Vina 推荐路径：

```text
resources/vina/vina.exe
```

旧路径 `resources/tools/vina/vina.exe` 只作为兼容回退。新发布材料和 manifest 应优先记录 `resources/vina/vina.exe`。

RDKit/Meeko 不随 V0.6 轻量包自动安装。可使用 `scripts/export_toolchain_environment.py` 导出推荐 conda 环境 yml，供用户或发布者复现 `dockstart-rdkit-meeko` 环境。

## Current Tauri Resource Policy

`apps/desktop/src-tauri/tauri.conf.json` 当前会把仓库根目录的 `resources/` 映射为打包资源：

```json
"resources": {
  "../../../resources/": "resources/"
}
```

这表示文档、manifest 和本地准备好的工具链文件可以进入本地安装包。是否将真实二进制加入安装包，必须由发布前检查决定，不能默认提交到 Git。

## Files That Must Stay Out Of Git

- `apps/desktop/dist/`
- `apps/desktop/src-tauri/target/`
- `target/`
- Windows installer `.msi` / `.exe`
- `resources/vina/vina.exe`
- `resources/vina/*.dll`
- `resources/tools/vina/vina.exe`
- `resources/python/python.exe`
- `resources/python/Lib/`
- `resources/python/DLLs/`
- `resources/python/Scripts/`
- `resources/python/site-packages/`
- `dockstart_settings.json`
- 真实 docking 输出和大型 raw 下载文件

## Release Build Script

V0.6.4 新增：

```powershell
cd E:\DockStart
scripts\build_windows_release.ps1
```

脚本会检查：

- 当前分支是否为 `main`；
- `git status --short` 是否干净；
- 版本号是否一致；
- 后端 unittest；
- 前端 `npm run build`；
- Rust/Tauri `cargo check`；
- `npm run tauri build`。

如只想验证脚本前半段，可使用：

```powershell
scripts\build_windows_release.ps1 -SkipTauriBuild
```

手动构建仍可执行：

```powershell
cd E:\DockStart\apps\desktop
npm run tauri build
```

构建产物通常位于：

```text
apps/desktop/src-tauri/target/release/
apps/desktop/src-tauri/target/release/bundle/
```

产物路径和大小必须记录到 release build report，但安装包本身不能提交进 Git。

V0.6.5 的本地构建验收记录见：

```text
docs/release/v0_6_5_build_report.md
```
