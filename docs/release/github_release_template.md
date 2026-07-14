# DockStart v0.10.2 Windows x64

> 发布前检查：把本文中的“待构建后填写 / 待门禁完成后填写”全部替换为真实结果。不得沿用旧版本的大小、SHA256 或验收结论。

DockStart v0.10.2 是计划发布的首个公开稳定版。它把 AutoDock Vina 的本地对接步骤整理为中文桌面工作流，并提供两个不能并行安装的 Windows x64 profile：

- **Basic Stable**：适合已经有 receptor/ligand PDBQT 的用户；
- **Assisted Stable**：额外随附独立、可替换的 CPython 3.11、RDKit 2026.3.3 与 Meeko 0.7.1，可离线尝试把受体 PDB/CIF 和配体 SDF/MOL 准备并转换为 PDBQT。

安装包 Author / Publisher：`XinXi Xu`。

## v0.10.2 重点改进

- 项目创建入口按实际输入改写为“已有 PDBQT”“PDB/CIF + SDF/MOL”“示例项目”，减少模式名称理解成本；
- 在线获取受体/配体成为原始结构流程中的常驻入口，可随时返回 RCSB/PubChem 搜索下载；
- Assisted 流程明确使用“准备并转换为 PDBQT”，并提供直接的受体/配体转换、进度、错误恢复和下一步引导；
- 减少结构准备页重复启动科学 Python 检测造成的等待，复用已缓存的工具链状态；
- 保留本地项目、对接箱体、Vina 参数、运行、构象查看、结果表格和 Markdown 报告闭环。

## 下载与选择

| 文件 | 适用对象 | 大小 | SHA256 | 发布门禁 |
| --- | --- | ---: | --- | --- |
| `DockStart_0.10.2_Basic_x64-setup.exe` | 已有 PDBQT，推荐安装方式 | 待构建后填写 | 待构建后填写 | 待门禁完成后填写 |
| `DockStart_0.10.2_Basic_x64_en-US.msi` | 已有 PDBQT，适合 MSI 部署 | 待构建后填写 | 待构建后填写 | 待门禁完成后填写 |
| `DockStart_0.10.2_Assisted_x64-setup.exe` | PDB/CIF + SDF/MOL，推荐安装方式 | 待构建后填写 | 待构建后填写 | 待门禁完成后填写 |
| `DockStart_0.10.2_Assisted_x64_en-US.msi` | PDB/CIF + SDF/MOL，适合 MSI 部署 | 待构建后填写 | 待构建后填写 | 待门禁完成后填写 |

Basic 与 Assisted 使用同一个应用身份，**请勿并行安装**。切换 profile 前先正常卸载当前版本；用户项目目录不应放在应用安装目录内。

四个安装包尚未进行 Authenticode 签名。Windows SmartScreen 或企业策略可能显示“未知发布者”；请核对 Release 页面上的文件名和 SHA256，安装包属性中的 Publisher 应为 `XinXi Xu`。

## 联网与离线边界

- 本地项目管理、PDBQT 导入、Assisted 本地格式准备、Box 设置、Vina 对接、结果解析和 Markdown 报告导出不需要联网；
- 使用 RCSB PDB ID 搜索/下载受体或 PubChem CID/名称搜索/下载配体时需要联网；
- DockStart 运行时不会联网安装 Python、RDKit、Meeko 或其他科学包，也不会自动修改系统 PATH；
- 用户配置的兼容 preparation Python 优先于 Assisted 随附工具链。

## 当前支持

| 环节 | 支持格式或能力 |
| --- | --- |
| Basic 对接输入 | receptor `.pdbqt`、ligand `.pdbqt` |
| Assisted 受体原始输入 | `.pdb`、`.cif` |
| Assisted 配体原始输入 | `.sdf`、`.mol` |
| 在线获取 | RCSB PDB ID；PubChem CID 或名称 |
| 结果输出 | docking PDBQT、`scores.csv`、Markdown 实验记录 |

当前不提供 MOL2/SMILES 自动准备、复杂受体修复、pocket prediction、PLIP/ProLIF 相互作用分析、Open Babel/MGLTools 内置、分子动力学、批量虚拟筛选或 AI 药效判断，也不修改 AutoDock Vina 算法或 scoring function。

## 发布门禁

- Python 测试：待填写；
- 前端生产构建：待填写；
- Cargo check/test/clippy：待填写；
- Basic 打包布局与真实 PDBQT/Vina 流程：待填写；
- Assisted development / post-package / post-install 三道门禁：待填写；
- Basic 与 Assisted GUI 人工流程及另一台干净 Windows 设备复验：待填写。

只有完成上述门禁、记录四个文件的真实大小和 SHA256 后，才把本候选标记为正式发布。

## 科学与许可证边界

自动生成 PDBQT 后仍需人工检查质子化、电荷、构象、缺失残基、水、金属、辅因子和链选择。对接箱体必须根据研究问题人工确认。

**Docking score 仅供结构结合趋势参考，不能替代实验验证，也不能证明真实结合、药效、安全性或临床价值。**

DockStart 自有代码采用 Apache-2.0；安装包内第三方组件遵循各自许可证。Meeko 0.7.1 以 LGPL-2.1 条款作为独立、可替换组件分发，详见安装包内 `resources/licenses/THIRD_PARTY_NOTICES.md`。许可证说明属于工程记录，不构成法律意见。
