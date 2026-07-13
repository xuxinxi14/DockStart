# DockStart v0.10.0 Windows Basic Stable Build Report

本报告记录 v0.10.0 Basic Stable 的重新构建与安装态验收。Basic 面向已经准备好 receptor/ligand PDBQT 的用户，随附 AutoDock Vina 与精简后端 Python，但不随附 RDKit/Meeko，也不提供 raw PDB/SDF 到 PDBQT 的格式准备能力。

## Build Provenance

- 验收时间：2026-07-14 07:10 +08:00
- 构建源码提交：`b768a2f`
- 安装包作者/Publisher：`XinXi Xu`
- OS：Microsoft Windows 11 家庭版 中文版 10.0.26200 build 26200
- 随附后端 runtime：CPython 3.11.15
- 随附 AutoDock Vina：1.2.7

## Authoritative Build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows_release.ps1 -Profile Basic
```

流水线退出码为 `0`，并完成：

- `324` 项 Python 后端测试；
- TypeScript/Vite 前端生产构建；
- Rust `cargo check`；
- Tauri MSI/NSIS 打包；
- 打包目录中的两轮真实 PDBQT/Vina 对接回归；
- Basic runtime 边界检查，确认未包含 Meeko、RDKit、NumPy、SciPy、`site-packages` 或 Python Scripts。

## Installed Validation

Basic NSIS 另行静默安装到仓库内的隔离目录后完成验收：

- 安装退出码为 `0`；
- 卸载记录 Publisher 为 `XinXi Xu`；
- 安装目录中的 bundled Python 与 Vina 可检测；
- `basic_mode_available=true`、`assisted_mode_available=false`；
- `run_001` 与 `run_002` 两轮真实 Vina 对接均为 `finished`；
- `vina_config.txt`、输入快照、metadata、日志、out.pdbqt、scores.csv 与 Markdown 报告均生成；
- 静默卸载退出码为 `0`；
- 安装目录、卸载记录和厂商注册表键均已清除。

## Artifacts

| File | Size | SHA256 | Manufacturer | Signature |
| --- | ---: | --- | --- | --- |
| `.release/artifacts/0.10.0/basic/DockStart_0.10.0_Basic_x64_en-US.msi` | 23,315,728 bytes | `5d1e85897c6d8335e4dc8f72348f4d2410741a1ce5dcd380b0fd3bb7f7cb3bac` | XinXi Xu | NotSigned |
| `.release/artifacts/0.10.0/basic/DockStart_0.10.0_Basic_x64-setup.exe` | 17,783,221 bytes | `83e0671d54ccccedd5a9b4fd1cb0e3fd019f553cbb107855841cbfe1ce8aaa9e` | XinXi Xu | NotSigned |

## Distribution Boundary

Basic 可以开箱完成：导入已有 PDBQT → 设置对接箱体与 Vina 参数 → 运行对接 → 查看构象和 scores → 导出 Markdown 报告。

Basic 不能开箱完成 PDB/SDF/MOL → PDBQT。需要该流程的用户应下载 Assisted Stable。两个 profile 使用同一应用身份，不应并行安装。

## Remaining Risks

- 安装包未进行 Authenticode 签名；
- 当前安装态验收在开发机的隔离目录完成，尚不能替代另一台干净 Windows 设备上的测试；
- 尚未验证旧版本原位升级；
- Basic 的真实安装验收目前是本轮人工发布检查，尚未像 Assisted 一样集成为构建脚本的强制 post-install gate。

Docking score 仅供结构结合趋势参考，不能替代实验验证。
