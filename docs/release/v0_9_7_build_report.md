# DockStart v0.9.7 Windows Basic Stable Build Report

本报告记录 v0.9.7 Basic Stable 在本机 Windows 环境中的构建和安装态验收。该版本把“开箱即用”限定为：用户已有受体/配体 PDBQT 时，可直接使用随应用提供的 AutoDock Vina 完成对接闭环。RDKit 与 Meeko 不在安装包中。

## Build Environment

- 验收时间：2026-07-14 00:36 +08:00
- OS：Microsoft Windows 11 家庭版 中文版 10.0.26200 build 26200
- Windows PowerShell：5.1.26100.8655
- Node.js：v24.14.1
- npm：11.11.0
- Rust：rustc 1.90.0
- Cargo：cargo 1.90.0
- Tauri CLI：2.11.3
- 构建 Python：3.13.14
- 随应用提供的后端 Python：3.11.15
- 随应用提供的 AutoDock Vina：1.2.7

## Authoritative Build

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows_release.ps1 -Profile Basic
```

连续流水线退出码为 `0`，并完成：

- 291 项 Python 后端测试；
- TypeScript 与 Vite 前端生产构建；
- `cargo check`；
- Tauri release 构建；
- MSI 与 NSIS 打包；
- 对 `target/release` 的两轮真实 Vina 对接回归；
- 当前版本安装包唯一性、大小和 SHA256 清单生成。

另行执行的 Rust 回归测试 `backend_python_commands_disable_bytecode_writes` 通过，确认 GUI 后端以 `python -B -m ...` 运行，并传递 `PYTHONDONTWRITEBYTECODE=1`。

## Artifacts

| File | Size | SHA256 | Signature |
| --- | ---: | --- | --- |
| `apps/desktop/src-tauri/target/release/bundle/msi/DockStart_0.9.7_x64_en-US.msi` | 19,943,795 bytes | `CE01D6C8060A5C5024D40D6F0CAED2D9173B59DE41202D064267D20CD563A77A` | NotSigned |
| `apps/desktop/src-tauri/target/release/bundle/nsis/DockStart_0.9.7_x64-setup.exe` | 15,026,915 bytes | `AF7196640BE9A04C71A499714895976E7E62DE13629EBF54AC798576F6539DB0` | NotSigned |

构建清单保存在忽略目录 `.release/basic/artifact-manifest.json`，其中产物路径使用仓库相对路径。

## Runtime Boundary

- Basic Mode：可用；随应用提供 Vina 与后端专用 Python。
- Demo Mode：可用；Basic PDBQT 示例可完成真实对接。
- Assisted Mode：安装包内不可用；需要用户另行配置带 RDKit/Meeko 的 Python 环境。
- 安装包不含 `site-packages`、Python `Scripts`、RDKit、Meeko、NumPy、SciPy 或 conda 环境。
- 未新增 PLIP、ProLIF、Open Babel、MGLTools、口袋预测、分子动力学、药效判断或 Vina 算法修改。

## Installed Validation

NSIS 被静默安装到带空格和中文的隔离目录 `E:\DockStart\.release\install tests\NSIS 中文`：

- 安装退出码为 `0`，版本登记为 0.9.7；
- GUI 启动 8 秒后仍正常运行；
- GUI 启动前后，安装树中的 `*.pyc`、`*.pyo` 和 `__pycache__` 数量均为 `0`；
- 安装态 Basic 示例完成 `run_001` 与 `run_002` 两轮真实 Vina 对接；
- 配置、输入快照、命令、版本、时间戳、SHA256、stdout、stderr、log、输出 PDBQT、scores.csv 和 Markdown 报告均通过验证；
- 静默卸载退出码为 `0`，安装目录和卸载注册表项均已移除。

最终 MSI 使用 `msiexec /a` 解包到独立目录后执行相同 Basic 回归，两轮对接均完成，解包树保持零 Python 字节码缓存。

前端另以真实浏览器在 1440、1200 和 960 像素宽度下完成暗色/亮色主题、横向溢出、控制可访问名称及工具链边界文案检查；无控制台错误。

## Known Warnings And Remaining Gates

- MSI 与 NSIS 未做 Authenticode 签名，Windows SmartScreen 或企业策略可能显示“未知发布者”。
- Vite 仍报告上游 3Dmol 的 `eval` 警告及 586.79 kB viewer chunk 警告；本轮未发现对应运行错误。
- 尚未在无开发工具、断网的全新 Windows 虚拟机上分别执行安装态 GUI 全流程。
- 尚未验证从带旧 Full runtime 残留的 v0.9.6 安装目录原位升级到 v0.9.7。
- 尚未创建或推送 `v0.9.7` tag，也未发布 GitHub Release；当前结论是本地稳定候选通过，不代表已经公开发布。

## Scientific Boundary

Docking score 仅供结构结合趋势参考，不能替代实验验证。此处的回归结果仅证明软件流程与产物完整性，不证明真实结合、药效、安全性或临床有效性。
