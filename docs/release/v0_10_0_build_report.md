# DockStart v0.10.0 Windows Assisted Stable Build Report

本报告记录 v0.10.0 Assisted Stable 在本机 Windows 环境中的正式构建、安装态验收和发布边界。该 profile 在 Basic PDBQT 对接闭环之上，随附独立、可替换的 CPython/RDKit/Meeko 工具链，用于离线完成 PDB 受体和 SDF/MOL 配体的最小 PDBQT 准备流程。

## Build Provenance

- 验收时间：2026-07-14 07:10 +08:00
- 构建源码提交：`96d6c6b`
- 安装包作者/Publisher：`XinXi Xu`
- OS：Microsoft Windows 11 家庭版 中文版 10.0.26200 build 26200
- Windows PowerShell：5.1.26100.8655
- Node.js：v24.14.1
- npm：11.11.0
- Rust：rustc 1.90.0
- Cargo：cargo 1.90.0
- Tauri CLI：2.11.3
- 构建 Python：3.13.14
- 随附后端/准备 runtime：CPython 3.11.15
- 随附 AutoDock Vina：1.2.7
- 随附 Meeko：0.7.1
- 随附 RDKit：2026.3.3

## Authoritative Build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows_release.ps1 -Profile Assisted
```

连续流水线退出码为 `0`。`.release/assisted/artifact-manifest.json` 的最终状态为：

- `development_gate=passed`
- `post_package_gate=passed`
- `post_install_gate=passed`
- `release_status=passed`
- `publishable=true`

## Validation Results

- Python：`325` 项后端测试通过；
- Rust：`13` 项测试通过；
- Rust 静态检查：`cargo clippy --locked --all-targets -- -D warnings` 通过；
- Rust 格式检查：`cargo fmt --check` 通过；
- 前端：TypeScript 与 Vite 生产构建通过，3Dmol 被拆分为按需加载 chunk；
- development gate：断网代理条件下，PDB 受体经 Meeko、SDF 配体经 RDKit/Meeko 准备后完成真实 Vina、结果解析与报告导出；
- post-package gate：在 Tauri `target/release` 资源布局中重复同一完整流程并通过；
- post-install gate：NSIS 静默安装到隔离目录后，从真实安装目录重复同一完整流程并通过；
- 工具优先级：验证用户配置的兼容 Python 优先，随附 runtime 可作为离线 fallback；
- 字节码洁净度：三道流程均未生成 `.pyc`、`.pyo` 或 `__pycache__`；
- GUI 浏览器验收：暗色默认主题、亮色切换、收起侧栏、工具链错误回退和首页初始不加载 3Dmol 均通过，控制台无错误或警告；
- 原生 GUI smoke：release 可执行文件启动 8 秒后仍响应，窗口标题为 `DockStart`。

post-install 回归产生的 `run_001` 状态为 `finished`。记录的 affinity 仅来自软件回归样例，不具有科学结论意义。

## Artifacts

| File | Size | SHA256 | Signature |
| --- | ---: | --- | --- |
| `.release/artifacts/0.10.0/assisted/DockStart_0.10.0_Assisted_x64_en-US.msi` | 113,132,764 bytes | `5a8f74bec929254d1517cb75e5e3380cb6a85a35e5db4089eefd1aa1c138119d` | NotSigned |
| `.release/artifacts/0.10.0/assisted/DockStart_0.10.0_Assisted_x64-setup.exe` | 73,161,918 bytes | `07121cd5407859b38b3bd4dbe7b17d13ee750e07c32030f3c7b43c386bacf494` | NotSigned |

发布清单保存在忽略目录 `.release/assisted/artifact-manifest.json`。清单中的路径均为仓库相对路径，文件大小和 SHA256 已在构建后再次独立核对。

## Installed Validation And Cleanup

本轮以 NSIS 作为正式真实安装门禁：

- 安装方式：NSIS 静默安装到 `.release/install-gate/installed/`；
- MSI Manufacturer、NSIS Publisher 与安装注册表厂商键均为 `XinXi Xu`；
- 安装态验证：离线准备、Vina、解析和报告流程通过；
- 静默卸载退出码：`0`；
- 安装目录已删除；
- bundled runtime 无残留；
- NSIS 卸载注册记录无残留；
- manufacturer registry key 已移除；
- 未执行失败后的强制清理；
- 最终残留计数：`0`。

MSI 已生成并通过 post-package 内容检查，但本次真实安装/卸载门禁使用的是 NSIS，不应把该结果表述为 MSI 安装态验收。

## Runtime And License Boundary

- Meeko 以普通 `Lib/site-packages/meeko/` 目录存在，通过独立 Python 子进程调用；
- 运行时完整性 hash 用于 provenance、缓存与诊断，不阻止用户替换兼容的 Meeko；
- 安装资源附带 Meeko 0.7.1 对应源代码、LGPL-2.1 文本、第三方 notices 和固定来源/SHA256 清单；
- CPython base runtime 是本机已审计快照，过滤后文件树 SHA256 为 `a0083111583074ffca8fd382cd59519d0d641360bf50be8880238a4d0dd6cdd7`；它没有被表述为可由 python.org 单一官方归档独立复现的产物；
- Basic 与 Assisted 使用同一应用身份，不能作为两个并行安装的软件版本；
- 未加入 Open Babel、MGLTools、PLIP、ProLIF、批量筛选、分子动力学、AI 药效预测或 Vina 算法修改。

## Known Warnings And Remaining Gates

- MSI 与 NSIS 尚未进行 Authenticode 签名，Windows SmartScreen 或企业策略可能显示“未知发布者”；
- Vite 仍报告 3Dmol 上游使用 `eval` 以及约 587 kB 的 viewer chunk 警告；该 chunk 已按需加载，首页首屏不会请求它；
- AutoDock Vina 不支持搜索过程中的 checkpoint/resume；当前恢复机制能识别并保护未完成 run，但不能从中断点继续搜索；
- 正在执行的 Vina 可取消，排队任务可取消；运行中的结构准备任务还没有等价的强制终止能力；
- 尚未在无任何开发工具的全新 Windows 虚拟机中进行人工 GUI 全流程验收；
- 尚未验证旧版原位升级路径；
- 尚未创建或推送 `v0.10.0` tag，也未发布 GitHub Release；本报告只证明本地稳定候选通过。

## Scientific Boundary

自动准备结果必须由用户复核质子化、电荷、构象、缺失残基、水、金属、辅因子和链选择。Docking score 仅供结构结合趋势参考，不能替代实验验证，也不能证明真实结合、药效、安全性或临床有效性。
