# DockStart v0.6.5 Windows Build Report

本报告记录 V0.6.5 本地 Windows 安装包构建验收。构建目标是验证 DockStart 桌面端可以生成安装包；安装包、`dist/` 和 `target/` 不提交到 Git。

## Build Environment

- OS: Microsoft Windows 11 家庭版 中文版 10.0.26200 build 26200
- Node.js: v24.14.1
- npm: 11.11.0
- Rust: rustc 1.90.0 (1159e78c4 2025-09-14)
- Cargo: cargo 1.90.0 (840b83a10 2025-07-30)
- Tauri CLI: tauri-cli 2.11.3
- Python: Python 3.13.14

## Commands

首次执行发布脚本：

```powershell
cd E:\DockStart
scripts\build_windows_release.ps1
```

结果：脚本在版本一致性检查阶段暴露 Windows PowerShell 5 对 `package-lock.json` 空字符串 package key 的 `ConvertFrom-Json` 兼容问题。V0.6.5 已将版本读取逻辑修复为：正常 JSON 解析优先，失败时回退读取顶层 `version` 字段。

修复后执行真实 Tauri 构建，并在提交前后各验证一次。最终干净工作区中的发布脚本执行成功：

```powershell
cd E:\DockStart
scripts\build_windows_release.ps1
```

## Build Result

构建成功。

产物：

| File | Size |
| --- | ---: |
| `apps/desktop/src-tauri/target/release/bundle/msi/DockStart_0.6.5_x64_en-US.msi` | 3,223,552 bytes |
| `apps/desktop/src-tauri/target/release/bundle/nsis/DockStart_0.6.5_x64-setup.exe` | 2,204,619 bytes |

## Toolchain Inclusion

- bundled Vina: not included. `resources/vina/vina.exe` was not present.
- bundled Python runtime: not included. `resources/python/python.exe`, `Lib/`, `DLLs` and `site-packages` were not present.
- conda environment: not included.
- user settings: not included.
- real docking outputs: not included.

## Packaging Fixes

- Added explicit Tauri bundle icon configuration using the existing `apps/desktop/src-tauri/icons/icon.ico`.
- Fixed `scripts/build_windows_release.ps1` version parsing for PowerShell 5 and `package-lock.json`.

## Known Warnings

- Vite reports a 3Dmol `eval` warning from `node_modules/3dmol/build/3Dmol.js`.
- Vite reports the main JS chunk is larger than 500 kB after minification.

These warnings are already known from frontend builds and do not indicate that PLIP/ProLIF, interaction analysis, pocket prediction, or new scientific functionality was added.

## Git Hygiene

Confirmed installer outputs remain under ignored Tauri build directories:

- `apps/desktop/dist/`
- `apps/desktop/src-tauri/target/`

They must not be committed.
