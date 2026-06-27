# Release Checklist

DockStart 发布前必须逐项检查。V0.6 当前目标是 Windows 打包和发布材料准备，不新增科学功能。

## Git And Version

- 当前分支是 `main`。
- `git status --short` 干净。
- 版本号一致：
  - `backend/dockstart_core/__init__.py`
  - `apps/desktop/package.json`
  - `apps/desktop/package-lock.json`
  - `apps/desktop/src-tauri/Cargo.toml`
  - `apps/desktop/src-tauri/Cargo.lock`
  - `apps/desktop/src-tauri/tauri.conf.json`
  - `apps/desktop/src/navigation/pages.ts`
- tag 已创建并推送。

## Validation Commands

```powershell
cd E:\DockStart
python -m unittest discover -s backend/tests
cd apps\desktop
npm run build
cd E:\DockStart
cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml
```

如果执行安装包构建，还需要记录：

```powershell
cd E:\DockStart
scripts\build_windows_release.ps1
```

## Toolchain

- bundled Vina 状态已检查。
- bundled Python runtime 状态已检查。
- 用户配置 Python/RDKit/Meeko 路径说明清楚。
- DockStart 不自动安装 RDKit/Meeko。
- `resources/toolchain_manifest.json` 未损坏。
- 第三方许可证说明已更新。

## Artifacts

确认没有误提交：

- installer `.msi` / `.exe`
- `dist/`
- `target/`
- `node_modules/`
- `__pycache__/`
- `dockstart_settings.json`
- `python.exe`
- `Lib/`
- `DLLs/`
- `site-packages/`
- conda env
- 真实 docking 输出
- 大型 raw/downloaded structures
- 第三方源码 zip

## Product Boundaries

发布说明必须明确：

- 没有新增科学功能；
- 没有接入 PLIP/ProLIF；
- 没有相互作用分析；
- 没有药效判断；
- 没有 pocket prediction；
- 没有分子动力学；
- 没有修改 AutoDock Vina 算法或 scoring function；
- 没有使用外部 CDN；
- 没有关闭 SSL 校验。

