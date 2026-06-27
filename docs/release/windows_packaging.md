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

## Manual Build Preview

V0.6.4 会提供正式脚本。在此之前，开发者可手动执行：

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

