# Release Artifact Capability Profile

DockStart v0.10.2 保留两个隔离的 Windows x86_64 发布 profile。二者都包含桌面端、
AutoDock Vina 1.2.7、DockStart 后端和小型示例，但 Python 工具链边界不同。
安装包文件名必须包含 `Basic` 或 `Assisted`；两个 profile 使用同一应用身份，不能并行安装。

## Basic Stable

```json
{
  "app_version": "0.10.2",
  "release_profile": "basic_stable",
  "includes_bundled_vina": true,
  "includes_bundled_python": true,
  "bundled_python_role": "backend_runtime",
  "includes_bundled_rdkit": false,
  "includes_bundled_meeko": false,
  "includes_conda_env": false
}
```

Basic Stable 面向已有 `prepared/receptor.pdbqt` 与 `prepared/ligand.pdbqt` 的用户。
它可离线完成对接箱体设置、Vina 参数配置、真实 Vina 运行、结果解析和 Markdown 报告导出。
其 Python runtime 必须排除 `Lib/site-packages`、`Scripts`、RDKit、Meeko 和 Python bytecode。

## Assisted Stable

```json
{
  "app_version": "0.10.2",
  "release_profile": "assisted_stable",
  "includes_bundled_vina": true,
  "includes_bundled_python": true,
  "bundled_python_role": "backend_and_preparation_runtime",
  "includes_bundled_rdkit": true,
  "includes_bundled_meeko": true,
  "includes_conda_env": false,
  "preparation_python_priority": ["configured", "bundled", "current_environment"]
}
```

Assisted Stable 在独立、可替换的 CPython 3.11 runtime 中固定 RDKit 2026.3.3、
Meeko 0.7.1 及其审计后的依赖，可离线从 PDB 受体与 SDF/MOL 配体尝试生成 PDBQT。
它不会把 Meeko 冻结进 `dockstart-desktop.exe`，也不会在应用运行时联网安装包。

Assisted 产物只有在以下三道门禁均为 `passed` 且
`.release/assisted/artifact-manifest.json` 的 `publishable` 为 `true` 时才可发布：

1. `development`：对白名单 stage 执行真实准备、对接、解析和报告流程；
2. `post-package`：对 Tauri `target/release` 资源布局重复相同流程；
3. `post-install`：真实静默安装 NSIS，从安装目录验证，再静默卸载并确认无残留。

## 示例与科学边界

安装包只以 `resources/examples/` 作为运行时示例源：

- `basic_pdbqt/`：已有 PDBQT 的最小真实 Vina 流程；
- `assisted_raw/`：PDB + SDF 的最小准备流程；
- `viewer_result/`：已完成结果的只读查看流程。

示例只验证软件工作流，不用于科研结论。自动准备结果仍需人工检查质子化、电荷、构象、
缺失残基、水、金属、辅因子和链选择。Docking score 仅供结构结合趋势参考，不能替代实验验证。

## 两个 profile 都不包含

- Open Babel、MGLTools、PLIP 或 ProLIF；
- pocket prediction、分子动力学、批量虚拟筛选或 AI 药效预测；
- 对 AutoDock Vina 算法或 scoring function 的修改；
- conda 环境或运行时联网安装器；
- “生成 PDBQT 即科学正确”或“docking score 证明真实结合/药效”的声明。
