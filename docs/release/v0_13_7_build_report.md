# DockStart v0.13.7 本地 GUI 候选包构建记录

日期：2026-08-03

## 候选包范围

本轮只生成 Assisted Windows 候选包。5X72 原始受体、BACE_1 MOL2、大环准备和其他原始结构转换依赖随附的 RDKit/Meeko，因此没有重复生成与本轮验收目标无关的 Basic 安装包。

本记录不是公开 Release 声明。真实安装/卸载门禁没有执行，`artifact-manifest.json` 保持 `publishable = false`。

## 主要修复

- 受体准备能够同时返回不完整残基和 alternate location 审查项；5X72 可在明确确认 `A:29` 并选择 `A:133=A` 后完成准备。
- 大环配体准备改为常驻四步流程：分析候选、选择方案、确认断环、转换 PDBQT；完成后在同一模块直接进入搜索范围设置。
- 柔性受体准备完成状态压缩为对齐的摘要行，不再重复显示大段说明。
- Tauri 打包脚本不再把旧 `target/release/DockStart` 当作打包后资源布局；MSI 通过管理提取获得真实内容后再执行 post-package 验证。

## 定向代码与界面检查

| 检查 | 结果 |
|---|---|
| 受体、配体、大环、结构导入定向后端测试 | 89 项通过，5 项跳过 |
| TypeScript / Vite 生产构建 | 通过 |
| Cargo check | 通过 |
| Playwright 柔性受体与大环布局检查 | 通过；0 console errors，0 warnings |
| 文档宽度 / 大环模块宽度 | `1728/1728`、`1030/1030`，无横向溢出 |

## 官方示例 3–6 实跑

| 示例 | 本轮结果 | 证据 |
|---|---|---|
| 3. 5X72 串行批量对接 | 原始受体严格准备同时识别 `A:29` 与 `A:133`；选择 `A:133=A` 并确认 `A:29` 后准备成功。官方两个配体均完成独立 Vina run。 | `output/qa/v0.13.7/batch-public-api/`、`output/qa/v0.13.7/batch-run/` |
| 4. BACE_1 大环对接 | 标准准备按预期停止并要求审查；候选分析、确认、PDBQT 准备、真实 Vina run、scores 与 Markdown 报告均完成。 | `output/qa/v0.13.7/macrocycle-public-api/` |
| 5. 1UW6 水合对接 | 固定官方输入完成水合配体、W map、AD4 run、姿势恢复与报告；本轮返回 7 个合法 Mode。 | `output/qa/v0.13.7/official-examples/hydrated-pinned.stdout.json` |
| 6. 1IEP AutoDock4 maps | AutoGrid4 maps、Vina AD4 run、9 个评分与报告完成；最佳分数 `-14.72 kcal/mol`。 | `output/qa/v0.13.7/official-examples/ad4-maps-1iep.json` |

## 打包检查

1. Assisted stage 从固定离线 wheelhouse 重建，未使用网络；stage 包含 5101 个文件、301,224,459 bytes。
2. 第一次 post-package 检查发现旧 NSIS 临时目录残留 125 个 `.pyc`；该候选包被保留到 `.release/stale/v0.13.7-rejected-bytecode-20260803-1730/`，没有归档为最终候选。
3. 清理逻辑加入 `target/release/DockStart` 后重新构建；新布局在验证前 `__pycache__ = 0`、`.pyc = 0`。
4. MSI 通过 `msiexec /a` 管理提取到隔离目录，提取内容完成真实 CIF 受体准备、SDF 配体准备、Vina run、评分与报告验证；`no_generated_bytecode = true`。
5. 使用 MSI 内的 Python/Meeko 再次转换官方 `5x72_receptorH.pdb`：严格模式返回 `A:29` 与 `A:133`，提交 `A:29` 确认和 `A:133=A` 后生成 119,799-byte PDBQT。证据：`output/qa/v0.13.7/msi-5x72.json`。
6. 未触碰 `C:\Users\19701\AppData\Local\DockStart` 中的既有安装，也没有执行真实安装/卸载门禁。

## 最终本地候选产物

| 文件 | 大小（bytes） | SHA256 |
|---|---:|---|
| `DockStart_0.13.7_Assisted_x64_en-US.msi` | 114,139,368 | `c8138705b6cefd0b8ae12bf4a523c349153e13dbd815d8a77f50ea30436da070` |
| `DockStart_0.13.7_Assisted_x64-setup.exe` | 73,836,877 | `0a1a8ed53201873414a95f5e5348f3232fe170dc263b497824c44c84a074cd51` |

产物目录：`.release/artifacts/0.13.7/assisted/`  
校验表：`.release/artifacts/0.13.7/SHA256SUMS.txt`

## 尚未完成的发布门禁

- 未运行全部后端、Rust 测试和 clippy；本轮按要求只执行定向检查。
- 未执行真实 NSIS 安装、从安装目录复跑、静默卸载和残留检查。
- 因此该产物仅用于用户手动验收，不应作为公开 Release 发布。
