# Release Artifact Capability Profile

DockStart V0.8.6 开始在发布材料中明确描述安装包“包含什么、不包含什么、预期能完成哪种模式”。V0.8.7 冻结审计继续沿用这份能力档案。这不是新增科学功能，而是避免把“开箱即用”误解为“无需任何外部条件即可自动准备所有分子”。

## 当前 V0.8.7 轻量/工具链辅助发布档案

```json
{
  "app_version": "0.8.7",
  "build_type": "lightweight_or_toolchain_assisted",
  "includes_bundled_vina": false,
  "includes_bundled_python": false,
  "includes_conda_env": false,
  "includes_demo_projects": true,
  "includes_examples": true,
  "basic_mode_expected": "需要 AutoDock Vina，以及用户已有 receptor/ligand PDBQT。",
  "assisted_mode_expected": "需要 AutoDock Vina，以及用户配置的 Python + RDKit + Meeko 环境。",
  "known_requirements": [
    "不包含 PLIP/ProLIF",
    "不包含 Open Babel/MGLTools",
    "不做药效判断",
    "轻量发布不包含 conda env"
  ]
}
```

## Basic Mode 条件

Basic Mode 是最低依赖路径：

- DockStart 桌面应用；
- AutoDock Vina 可用；
- 用户已有 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`。

RDKit/Meeko 缺失不应阻止 Basic Mode。

## Assisted Mode 条件

Assisted Mode 用于从 raw PDB/CIF/SDF/MOL 尝试生成 PDBQT：

- DockStart 桌面应用；
- AutoDock Vina 可用；
- Python 可用；
- RDKit 可 import；
- Meeko 可 import 且准备能力可检测。

DockStart 当前不会自动安装 RDKit/Meeko，不提交 conda env，也不保证自动准备结果科学正确。

## Demo Mode 条件

Demo Mode 依赖仓库内的小型示例项目：

- `examples/demo_basic_project/`；
- `examples/demo_assisted_project/`。

示例项目只用于软件流程演示，不用于科研结论。没有真实 Vina 时，示例仍可用于非运行演示，但不能完成真实 docking。

## 发布说明必须避免的误导

发布说明不能暗示：

- DockStart 自动判断药效；
- DockStart 已接入 PLIP/ProLIF；
- DockStart 会自动安装 RDKit/Meeko；
- 生成 PDBQT 等于科学正确；
- docking score 能证明真实结合；
- 轻量安装包包含 conda env、完整 Python runtime 或 site-packages。
