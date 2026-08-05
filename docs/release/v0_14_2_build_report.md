# DockStart v0.14.2 Windows 本地候选构建报告

构建日期：2026-08-05（Asia/Shanghai）

## 结论

本轮从 `main` 分支的提交 `faffb30168060b346f08cebb917e4d7c83dd277b` 重新生成了
Windows x64 Basic 与 Assisted 两组 v0.14.2 安装包。构建时工作树包含尚未提交的版本和功能修改，
因此两组 manifest 均记录 `worktree_dirty=true`、`publishable=false`，只可作为本地候选，不能公开发布。

Basic 的开发、打包和 MSI 解包后真实对接门禁通过。Assisted 的开发态及 MSI 解包后真实
PDB/CIF + SDF 准备与 Vina 对接门禁通过；为保护当前已经注册的 DockStart 安装，本轮显式使用
`-SkipPostInstallGate`，没有执行 NSIS 真实安装/卸载门禁，所以 Assisted 状态为
`candidate_incomplete`。

## 构建命令

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows_release.ps1 `
  -Profile Basic -AllowDirtyDevelopmentBuild

powershell -ExecutionPolicy Bypass -File scripts\build_windows_assisted_release.ps1 `
  -AllowDirtyDevelopmentBuild -SkipPostInstallGate
```

构建前的安全检查确认所有清理目录都位于 `.release/`。系统中已有的 DockStart 安装记录指向
`E:\DockStart\apps\desktop\src-tauri\target\release\DockStart`，未被本轮构建覆盖或删除。

## Basic 候选

- Candidate ID：`0.14.2-faffb301-20260805T143125Z-dirty`
- Release status：`candidate`
- Development gate：通过
- Post-package gate：通过
- Post-install gate：`not_applicable_basic_candidate`
- Python unittest：1273 项通过，6 项跳过
- 前端：TypeScript 与 Vite 生产构建通过
- Rust：`cargo check` 通过
- Tauri：MSI 与 NSIS 生成通过
- 包内回归：MSI 行政解包后 Basic Vina 对接重复运行通过；首次最佳结合能为 `-0.6917 kcal/mol`，
  生成 8 个构象

| 文件 | 大小（bytes） | SHA-256 |
| --- | ---: | --- |
| `DockStart_0.14.2-faffb301-20260805T143125Z-dirty_Basic_x64_en-US.msi` | 24,391,964 | `73e303f78b5d48a48f7157cee79e0cf452cf75e18b7feb55abbf563375ccb34e` |
| `DockStart_0.14.2-faffb301-20260805T143125Z-dirty_Basic_x64-setup.exe` | 18,490,491 | `2ae4f5ee4e8d1c875826b6a13e1229268c3d0e31e2ee8379a82e8cd642cdb0db` |

Manifest：
`.release/artifacts/0.14.2/0.14.2-faffb301-20260805T143125Z-dirty/basic/artifact-manifest.json`

## Assisted 候选

- Candidate ID：`0.14.2-faffb301-20260805T145053Z-dirty`
- Release status：`candidate_incomplete`
- Development gate：通过
- Post-package gate：通过
- Post-install gate：`skipped_development_only`
- Python unittest：1273 项通过，6 项跳过
- Rust unittest：35 项通过
- 前端：TypeScript 与 Vite 生产构建通过
- Rust：`cargo check` 与 `cargo test` 通过
- Tauri：MSI 与 NSIS 生成通过
- 开发态科学闭环：离线 CIF 受体和 SDF 配体准备、Vina 对接通过，最佳结合能为
  `-0.6908 kcal/mol`
- 包内科学闭环：MSI 行政解包后离线准备和 Vina 对接通过，最佳结合能为
  `-0.6909 kcal/mol`
- Bundled runtime：CPython 3.11.15、AutoDock Vina 1.2.7、RDKit 2026.3.3、Meeko 0.7.1

| 文件 | 大小（bytes） | SHA-256 |
| --- | ---: | --- |
| `DockStart_0.14.2-faffb301-20260805T145053Z-dirty_Assisted_x64_en-US.msi` | 114,192,616 | `c0c3f5e43c863500b697c3b6c4f4c2682999d6aa65367452b5c69e26cd6db67e` |
| `DockStart_0.14.2-faffb301-20260805T145053Z-dirty_Assisted_x64-setup.exe` | 73,861,423 | `b37461b1328cd65d26420e2d90b8481bd895798354ef271d920be4f6648d0332` |

Manifest：
`.release/artifacts/0.14.2/0.14.2-faffb301-20260805T145053Z-dirty/assisted/artifact-manifest.json`

## 独立复核

构建完成后重新读取四个安装包并独立计算 SHA-256 与文件大小，结果均与各自
`artifact-manifest.json` 一致。七个权威版本字段均为 `0.14.2`，`git diff --check` 未发现空白错误。

## 发布限制与后续门禁

- 两组候选都来自 dirty worktree，未做代码签名，不能作为正式公开 Release。
- Assisted 未执行真实安装/静默卸载门禁；如需发布，必须先处理当前已安装版本，在干净 `main`
  工作树上重新构建，并运行默认的 post-install gate。
- 两组安装包都不包含 AutoGrid4、`AD4Zn.dat`、Open Babel 或 MGLTools；相关外部输入仍由用户提供。
- 构建器不重复执行 1FPU、5X72 和固定 12 项串行 AD4 外部科学验收；这些证据沿用独立验收流程，
  不应由普通打包回归替代。
- Docking score 仅供结构结合趋势参考，不能替代实验验证。
