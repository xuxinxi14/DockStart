# Release Checklist

DockStart v0.10.2 的目标是稳定交付 Basic Stable 与 Assisted Stable，不扩展新的 docking
算法或科学结论能力。

## Git 与版本

- 当前分支为 `main`，`git status --short` 干净；
- 以下七处版本均为 `0.10.2`：后端 `__init__.py`、`package.json`、
  `package-lock.json`、`Cargo.toml`、`Cargo.lock`、`tauri.conf.json`、`pages.ts`；
- 本地候选验收不冒充 GitHub Release；只有明确发布时才创建并推送 tag；
- 安装包、`.release/`、`dist/`、`target/`、runtime 二进制和真实 docking 输出不提交 Git。

## 通用质量门禁

```powershell
python -m unittest discover -s backend/tests

Push-Location apps/desktop
npm run build
Pop-Location

cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
cargo clippy --manifest-path apps/desktop/src-tauri/Cargo.toml --all-targets -- -D warnings
```

- 项目配置迁移、原子写入、revision 冲突、准备任务并发和崩溃恢复测试通过；
- 工具链缓存显式重检、后台队列、取消、任务重连和路径边界测试通过；
- 3Dmol 为动态 chunk，普通页面首屏不应加载它；
- 无新增未记录依赖，第三方 notice 与许可证文件随两个 profile 一起打包。

## Basic Stable

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -Profile Basic
```

- `.release/basic/` 从空目录白名单生成；
- 包含 Vina、后端 Python、许可证和示例；
- 不存在 `site-packages`、`Scripts`、RDKit、Meeko、`__pycache__`、`.pyc` 或 `.pyo`；
- `verify_basic_release.py` 在打包布局完成两次真实 Vina 运行、结果与报告回归。

## Assisted Stable

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -Profile Assisted
```

- wheelhouse 中每个 artifact 与 `resources/assisted/SOURCE_MANIFEST.json` 的文件名、URL、
  版本和 SHA256 一致；构建过程不联网、不解析浮动依赖；
- Meeko/RDKit 仍是独立、可替换的普通 Python 包；
- Meeko、Gemmi、tqdm 对应版本源码与全部运行时许可证/notices 随包；
- `development`、`post-package`、`post-install` 三道门禁全部为 `passed`；
- `artifact-manifest.json` 的 `publishable` 为 `true`；使用
  `-SkipPostInstallGate` 生成的开发产物不得发布。

## 安装与 GUI 验收

- NSIS 被真实安装到隔离目录，从安装目录运行完整 Assisted 流程后静默卸载，无安装目录、
  bundled Python 或卸载注册记录残留；
- MSI/NSIS 文件名只包含当前版本，大小和 SHA256 记录到本轮 `v0.10.2` 构建报告；
- 启动实际桌面端，完成“打开 Assisted 示例 → 准备受体/配体 → 设置 Box/Vina 参数 →
  开始对接 → 查看结果与报告 → 重启再打开”；
- 验证工具链页默认使用 bundled fallback，显式重检会失效缓存；
- 验证页面切换不会重复启动 Python 或重复加载 3D 模型；
- 验证任务进行中关闭/重开页面可重连，应用进程异常退出后历史 run 能被标记恢复；
- 浅色/暗色、窗口默认尺寸、禁用文本选择与右键等既有 GUI 回归仍正常。

## 发布文案边界

- 明确 Basic 与 Assisted 的不同能力，不把后端 Python 等同于 preparation 工具链；
- 明确自动准备仍需人工检查，docking score 不能证明真实结合或药效；
- 明确不含 PLIP/ProLIF、Open Babel/MGLTools、相互作用分析、pocket prediction、
  分子动力学、批量筛选或 scoring function 修改；
- 许可证分析属于工程记录，不构成正式法律意见。
