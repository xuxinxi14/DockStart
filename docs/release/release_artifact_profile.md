# Release Artifact Capability Profile

DockStart V0.9.7 采用 **Basic Stable** 发布档案。安装包随附 AutoDock Vina、DockStart 后端 Python 和小型示例项目，面向已经准备好受体/配体 PDBQT 的用户提供开箱即用的本地对接闭环。

本版本不随包提供 RDKit 或 Meeko。Assisted Mode 的界面、能力检测和调用接口继续保留；需要从 raw PDB/CIF/SDF/MOL 准备 PDBQT 时，用户必须配置带 RDKit/Meeko 的独立 Python 环境。

## 当前 V0.9.7 发布档案

```json
{
  "app_version": "0.9.7",
  "build_type": "basic_distributable",
  "includes_bundled_vina": true,
  "includes_bundled_python": true,
  "bundled_python_role": "backend_runtime",
  "includes_bundled_rdkit": false,
  "includes_bundled_meeko": false,
  "includes_conda_env": false,
  "includes_demo_projects": true,
  "includes_examples": true,
  "basic_mode_expected": "安装包随附 AutoDock Vina；用户提供 receptor/ligand PDBQT 后可以完成本地对接闭环。",
  "assisted_mode_expected": "保留 Assisted Mode 接口；需要用户配置带 RDKit/Meeko 的独立 Python 环境。",
  "known_requirements": [
    "不包含 RDKit/Meeko preparation runtime",
    "不包含 PLIP/ProLIF",
    "不包含 Open Babel/MGLTools",
    "不做药效判断",
    "不包含 conda env"
  ]
}
```

## Basic Mode 条件

Basic Mode 是 V0.9.7 的稳定主路径：

- DockStart 桌面应用；
- 安装包随附并能正常运行的 AutoDock Vina，也允许用户改用外部配置路径；
- 用户已有 `prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt`。

满足这些条件后，可以离线完成 Box 设置、Vina 参数配置、对接执行、结果解析和 Markdown 报告导出。RDKit/Meeko 缺失不应阻止 Basic Mode。

## Assisted Mode 条件

Assisted Mode 用于从 raw PDB/CIF/SDF/MOL 尝试生成 PDBQT。V0.9.7 保留该流程的界面与调用接口，但不随包提供 RDKit/Meeko：

- 用户需要在设置页配置独立 Python 环境；
- 该环境需要能够 import RDKit；
- 该环境需要能够 import Meeko，并能检测到相应准备能力；
- 准备结果仍需人工检查质子化、电荷、构象、缺失残基、水、金属、辅因子和链选择。

DockStart 不自动安装或修改用户的 RDKit/Meeko 环境。Assisted Mode 是否可用必须由当前配置环境的实际检测结果决定，不能由安装包文件清单推断。

## Demo Mode 条件

Demo Mode 依赖安装包内的小型示例项目：

- `resources/examples/basic_pdbqt/`；
- `resources/examples/assisted_raw/`；
- `resources/examples/viewer_result/`。

`resources/examples/` 是安装包唯一的运行时示例源；仓库旧的 `examples/demo_*` 不作为发布档案判定依据。示例项目只用于软件流程演示，不用于科研结论。Basic 示例可用于验证随附 Vina 的真实运行闭环；Assisted 示例只有在用户配置 RDKit/Meeko 环境后才能执行自动准备。

## 发布说明必须避免的误导

发布说明不能暗示：

- V0.9.7 随包提供 RDKit、Meeko 或完整 preparation runtime；
- 后端 Python 等同于 Assisted Mode 工具链；
- DockStart 会自动安装或修改 RDKit/Meeko；
- 生成 PDBQT 等于科学正确；
- docking score 能证明真实结合或药效；
- DockStart 已接入 PLIP/ProLIF、Open Babel 或 MGLTools。
