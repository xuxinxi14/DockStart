# Release Checklist

DockStart 发布前必须逐项检查。v0.9.7 当前目标是 Windows Basic Stable 打包和稳定性验收，不新增科学功能。

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
- 正式公开发布时 tag 已创建并推送；本地候选验收不冒充已发布。

## Validation Commands

```powershell
python -m unittest discover -s backend/tests
Push-Location apps/desktop
npm run build
Pop-Location
cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml
```

如果执行安装包构建，还需要记录：

```powershell
scripts\build_windows_release.ps1 -Profile Basic
```

安装包产物路径和大小应记录到当前版本 build report，例如：

```text
docs/release/v0_9_7_build_report.md
```

如只验证发布脚本的检查段：

```powershell
scripts\build_windows_release.ps1 -Profile Basic -SkipTauriBuild
```

## Toolchain

- bundled Vina 状态已检查。
- backend-only bundled Python runtime 状态已检查。
- `.release/basic/` 从空目录生成，且不存在 `site-packages`、`Scripts`、RDKit、Meeko、`__pycache__` 或 Python bytecode。
- `resources/examples/basic_pdbqt` 已由打包后真实 Vina 闭环验证两次。
- 用户配置 Python/RDKit/Meeko 路径说明清楚。
- DockStart 不自动安装 RDKit/Meeko。
- `resources/toolchain_manifest.json` 未损坏。
- 第三方许可证说明已更新。

## Artifacts

确认没有误提交：

- installer `.msi` / `.exe`
- release installer 文件名模式，例如 `DockStart_*_x64-setup.exe`
- `dist/`
- `target/`
- `node_modules/`
- `__pycache__/`
- `dockstart_settings.json`
- `python.exe`
- `Lib/`
- `DLLs/`
- `Scripts/`
- `site-packages/`
- conda env
- 真实 docking 输出
- 大型 raw/downloaded structures
- 第三方源码 zip

## Packaged And Installed Smoke

- `scripts/verify_basic_release.py` 已对 `target/release` 通过。
- MSI 与 NSIS 文件名只包含当前版本，SHA256 已记录。
- 公开发布前已在无开发版 Python、无 PATH Vina、断网的干净 Windows 中分别验证 MSI 与 NSIS。
- GUI 已验证“打开 Basic 示例 → 运行 → 结果 → 报告 → 重启再打开”。
- 升级安装不会把旧 RDKit/Meeko 文件残留到 v0.9.7 Basic runtime。

## GitHub Release Materials

- `docs/release/github_release_template.md` 已更新。
- `docs/release/release_artifact_profile.md` 已更新。
- 安装包 SHA256 可用 `scripts/hash_release_artifacts.py` 生成。
- Release notes 明确列出 included / not included / known limitations。
- Release notes 明确 Basic Mode / Assisted Mode / Demo Mode 的可用条件。
- Release notes 不得暗示无需任何外部条件即可自动准备所有分子。
- Checksums 发布前已经替换占位符。

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
