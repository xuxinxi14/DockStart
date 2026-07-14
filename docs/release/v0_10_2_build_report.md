# DockStart v0.10.2 Windows 发布构建报告

构建日期：2026-07-15（Asia/Shanghai）  
源码提交：`fcbbd0548ab1ea4c3efdfedd3fb737ebbf962162`  
构建分支：`main`  
Author / Publisher 元数据：`XinXi Xu`

## 结论

同一源码提交已生成 Basic 与 Assisted 的 MSI、NSIS 四个 Windows x64 安装包。Basic 和 Assisted 的离线运行门禁均通过；Assisted 清单状态为 `publishable: true`。当前仍需在一台无开发环境依赖的干净 Windows 10/11 x64 设备上，对两个 MSI 做真实安装/卸载，并用最终四个文件复验 GUI 主流程。

## 通用门禁

- Python：`python -m unittest discover -s backend/tests`，334 项通过；
- 前端：TypeScript 检查与 Vite production build 通过，4636 个模块完成转换；
- Rust：`cargo check` 通过，14 项 `cargo test` 通过，`cargo clippy --all-targets -- -D warnings` 通过；
- Git：构建前工作区干净，版本字段一致为 `0.10.2`；
- 构建过程：Basic 与 Assisted 分别执行 `scripts/build_windows_release.ps1`，未复用安装包或跳过 Tauri 构建；
- 网络：Assisted 由本地固定 wheelhouse 构建，`network_used: false`。

前端构建保留两项非阻塞告警：3Dmol 上游包包含 `eval`；3Dmol chunk 大于 500 kB。二者未导致测试或打包失败，后续可单独优化加载边界。

## Basic Stable

- 随附 CPython 3.11.15 作为后端 runtime；
- 随附 AutoDock Vina 1.2.7；
- 明确不随附 Meeko、RDKit、NumPy、SciPy；
- 打包目录真实对接：连续两轮运行成功，生成 config、metadata、输入快照、stdout、log、PDBQT、scores.csv 与 Markdown 报告；
- NSIS：真实静默安装到隔离目录、安装后离线对接、静默卸载通过；安装目录、运行时与卸载记录均无残留；
- MSI：管理提取后的安装布局通过两轮离线对接。该验证确认 MSI 内容与运行时完整，但不等价于真实 MSI 安装/卸载。

## Assisted Stable

- 随附 CPython 3.11.15、RDKit 2026.3.3、Meeko 0.7.1、NumPy 1.26.4 与必要依赖；
- Meeko ligand/receptor CLI 均通过离线导入探测；
- 对应源码归档与许可证清单的 SHA256 校验通过；
- development gate：PDB 受体与 SDF 配体准备、PDBQT 生成、Vina 对接通过；
- post-package gate：从 Tauri release 布局重复完成同一离线闭环；
- post-install gate：NSIS 真实安装后完成原始结构准备、对接与报告，随后静默卸载且无残留；
- MSI：管理提取后的安装布局完成 PDB/SDF → PDBQT → Vina → 报告闭环。该验证不等价于真实 MSI 安装/卸载；
- 最终清单：`development_gate=passed`、`post_package_gate=passed`、`post_install_gate=passed`、`publishable=true`。

## 安装包与校验值

| Profile | 文件 | 大小 | SHA256 |
| --- | --- | ---: | --- |
| Basic | `DockStart_0.10.2_Basic_x64-setup.exe` | 17,789,525 B（16.97 MiB） | `7240a1b918ebfdf4053e37e0f26d0af12411adf62b7a5a3e43104bdcc25cee3f` |
| Basic | `DockStart_0.10.2_Basic_x64_en-US.msi` | 23,311,632 B（22.23 MiB） | `4de0858e59ddc5a2e9ea72fd316adc4a6fd69e6bebffd5e5eade3cc28db820f9` |
| Assisted | `DockStart_0.10.2_Assisted_x64-setup.exe` | 73,170,277 B（69.78 MiB） | `91d9d3c474768145d918925deb1f87bc5c9b526e322d2ddb6f25a85628e790a7` |
| Assisted | `DockStart_0.10.2_Assisted_x64_en-US.msi` | 113,120,476 B（107.88 MiB） | `4352962bed0be9ce09c4948fc4f77d1d770a6201e8985b8ab35a53870e760942` |

产物目录：`.release/artifacts/0.10.2/basic` 与 `.release/artifacts/0.10.2/assisted`。

## 发布前剩余门禁

1. 在干净 Windows 10/11 x64 设备上分别真实安装并卸载 Basic MSI、Assisted MSI；
2. 依次卸载前一个 profile，再用四个最终安装包复验创建项目、导入/转换、设置 Box、运行对接、查看结果与导出报告；
3. 核对安装界面与文件属性中的 `XinXi Xu` 元数据，并记录 SmartScreen 行为；
4. 通过后创建 `v0.10.2` 标签和 GitHub Release，上传四个安装包及 SHA256；
5. 当前产物未做 Authenticode 签名，公开下载时可能显示“未知发布者”。

Basic 与 Assisted 使用同一应用身份，不能并行安装。切换 profile 前必须先正常卸载当前版本。
