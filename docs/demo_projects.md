# DockStart Demo Projects

DockStart V0.9.6 的示例流程由 `resources/examples/` 提供。示例项目只用于学习 DockStart 操作流程，不用于药效判断、真实 docking 解释、课程或论文中的科学证据。

每个示例目录必须包含 `manifest.json`。前端示例卡片从 manifest 读取标题、说明、标签、所需工具、入口步骤和按钮文案，不在前端代码里硬编码示例信息。

## 示例类型

### 基础对接示例

目录：

```text
resources/examples/basic_pdbqt/
```

用途：

- 演示已有 `receptor.pdbqt` 和 `ligand.pdbqt` 的最低依赖路径；
- 复制后进入准备结构页；
- 只需要 AutoDock Vina 才能继续真实运行对接。

### 从原始结构开始示例

目录：

```text
resources/examples/assisted_raw/
```

用途：

- 演示从 `raw/receptor.pdb` 和 `raw/ligand.sdf` 进入结构准备；
- 复制后进入结构准备页；
- `prepared/` 下包含参考 PDBQT 文件，便于 RDKit / Meeko 不可用时继续体验后续流程。

### 结果查看示例

目录：

```text
resources/examples/viewer_result/
```

用途：

- 演示已完成 run 的结果页、分数表、日志、报告和构象查看入口；
- 复制后直接进入结果页；
- 不重新运行 AutoDock Vina。

## 复制规则

用户在示例流程页点击复制时，DockStart 会把示例复制到用户选择的工作区父目录。如果同名目录已存在，会自动生成不冲突名称，例如：

```text
basic_demo_001
basic_demo_002
```

复制后的 `project.json` 会更新项目名、项目路径和示例元信息。仓库内的 `resources/examples/` 模板不会被修改。

## 科学边界

当前示例文件是小型玩具数据，用于验证软件流程和界面跳转。Docking score 仅供结构结合趋势参考，不能替代实验验证。
