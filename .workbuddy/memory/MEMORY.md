# DockStart 项目长期笔记

## 项目定位
DockStart 是基于 AutoDock Vina 的第三方开源中文分子对接工作台。当前 V0.1.11，MVP 最小闭环已完成。不修改 docking 算法，只做图形化前端 + 流程管理 + 参数生成 + 运行 + 解析。目标用户：生技学生、药学/生信初学者、本科毕设。

## 三层架构
1. **前端** `apps/desktop/src/` — React 19 + Vite 7 + TypeScript 5.8。App.tsx 用 useState 切换 10 个页面（无路由库）。状态 currentProject + currentRunId 在顶层管理。
2. **Tauri 桥接** `apps/desktop/src-tauri/src/main.rs` — Rust"薄壳"，22 个 `#[tauri::command]`，每个命令 subprocess 调 `python -m dockstart_core.xxx <args>`，读 stdout JSON 返回。Rust 不做业务逻辑。`find_backend_dir()` 从 cwd/current_exe 向上找含 `backend/dockstart_core/tool_check.py` 的目录。
3. **Python 后端** `backend/` — 无 Web 框架，纯 CLI 模块。
   - `adapters/` — vina/python/meeko/rdkit/viewer_adapter，每个暴露 `detect() → ToolCheckResult`
   - `dockstart_core/` — `project.py`(核心，~2000行：项目/PDBQT/Box/Vina/Run/解析/报告)、`settings.py`、`tool_check.py`(聚合检测)、`models.py`(ToolCheckResult frozen dataclass)
   - `workflows/` 只有 `__init__.py`（MVP 未用）
   - `tests/` 4 个 unittest 模块

## 关键约定
- **adapter 模式**：外部工具必须经 adapter，业务逻辑不硬编码命令。AGENTS.md 列了 openbabel/pubchem adapter 但 V0.1 未实现（V0.2 计划）。
- **错误格式**：统一 `{ok:false, error:{code,message,raw_error,suggestion}}`，全中文友好提示。
- **路径安全**：所有项目文件用项目内相对路径，`_project_relative_file()` 校验路径不逃出项目根；禁止硬编码绝对路径。
- **subprocess 安全**：参数数组，不拼 shell；捕获 stdout/stderr；UTF-8。
- **设置持久化**：`dockstart_settings.json`（项目根），可用 `DOCKSTART_SETTINGS_PATH` 环境变量覆盖。
- **项目数据模型**：`project.json` + `runs/run_XXX/metadata.json`，runs 自增 `run_001/run_002...`。
- **科学免责**：报告必须含"Docking score 仅供结构结合趋势参考，不能替代实验验证。"
- **许可证**：本体计划 Apache 2.0；Open Babel/PLIP/MGLTools 是 GPL，只作外部可选，不内置。

## 运行方式
- 后端测试：`python -m unittest discover -s backend/tests`
- 前端开发：`cd apps/desktop && npm run dev`（Vite 127.0.0.1:1420）
- Tauri 桌面：`cd apps/desktop && npm run tauri dev`
- Cargo 检查：`cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`

## 路线图
- V0.1 ✅ 本地 PDBQT docking MVP
- V0.2 结构获取与准备（PDB/PubChem 下载、RDKit/Meeko 自动准备、更完善错误引导）
- V0.3 3Dmol.js/Mol* 可视化 + 可视化 box 设置
- V0.4 ProLIF/PLIP 相互作用分析
- V0.5 批量 docking 与结果管理
