# DockStart v0.10.2 Windows x64

> 构建来源：`19900f3ad172c8d4b5a583a18ec52a8c683a6322`。发布前请先在干净 Windows 设备上完成下述两项外部门禁，再创建 `v0.10.2` 标签。

DockStart v0.10.2 是计划发布的首个公开稳定版。它把 AutoDock Vina 的本地对接步骤整理为中文桌面工作流，并提供两个不能并行安装的 Windows x64 profile：

- **Basic Stable**：适合已经有 receptor/ligand PDBQT 的用户；
- **Assisted Stable**：额外随附独立、可替换的 CPython 3.11、RDKit 2026.3.3 与 Meeko 0.7.1，可离线尝试把受体 PDB/CIF 和配体 SDF/MOL 准备并转换为 PDBQT。

安装包 Author / Publisher：`XinXi Xu`。

## v0.10.2 重点改进

- 项目创建入口按实际输入改写为“已有 PDBQT”“PDB/CIF + SDF/MOL”“示例项目”，减少模式名称理解成本；
- 在线获取受体/配体成为原始结构流程中的常驻入口；RCSB PDB ID/关键词与 PubChem CID/名称均返回可选择候选，不再默认下载第一项；
- 候选结构支持只读 3D 预览，只有明确“选择并准备”后才写入项目；本地导入 PDB/CIF、SDF/MOL 也会立即尝试准备 PDBQT；
- Assisted 流程明确使用“准备并转换为 PDBQT”，并提供直接的受体/配体转换、进度、错误恢复和下一步引导；
- CIF 受体改由随附 Gemmi 转换为受约束中间 PDB 后交给 Meeko，避免未随包提供的 ProDy 依赖导致失败；
- 减少结构准备页重复启动科学 Python 检测造成的等待，复用已缓存的工具链状态；
- 界面显示 Basic/Assisted profile，保存与重新检查按钮移入 Vina 参数区，并增加“定位到当前配体”；
- 保留本地项目、对接箱体、Vina 参数、运行、构象查看、结果表格和 Markdown 报告闭环。

## 下载与选择

| 文件 | 适用对象 | 大小 | SHA256 | 发布门禁 |
| --- | --- | ---: | --- | --- |
| `DockStart_0.10.2_Basic_x64-setup.exe` | 已有 PDBQT，推荐安装方式 | 17,804,745 B（16.98 MiB） | `eb4c8c12b73a84a46de3ea4db7c1b6c94628adc88a7c2dbd92200d97fd91780a` | NSIS 真实安装、两轮离线对接、卸载清理通过 |
| `DockStart_0.10.2_Basic_x64_en-US.msi` | 已有 PDBQT，适合 MSI 部署 | 23,340,449 B（22.26 MiB） | `5e406fb920c9508dbe8c2ee5083895b3c9b062cec45c717264f6c8771d817047` | 内容提取与两轮离线对接通过；待干净机安装/卸载 |
| `DockStart_0.10.2_Assisted_x64-setup.exe` | PDB/CIF + SDF/MOL，推荐安装方式 | 73,191,393 B（69.80 MiB） | `e38aca94f74bbe13d259b93e8da6910918f671276dacb0be5d0e5fa6ac731f71` | 三道流程门禁、CIF 准备及真实安装/卸载通过 |
| `DockStart_0.10.2_Assisted_x64_en-US.msi` | PDB/CIF + SDF/MOL，适合 MSI 部署 | 113,145,197 B（107.90 MiB） | `dfc147d8af290e6dfdacdb244d60e92afba9b7dd687412980edf6fbacb0c9b75` | 内容提取及 CIF/SDF 准备、对接、报告通过；待干净机安装/卸载 |

Basic 与 Assisted 使用同一个应用身份，**请勿并行安装**。切换 profile 前先正常卸载当前版本；用户项目目录不应放在应用安装目录内。

四个安装包尚未进行 Authenticode 签名。Windows SmartScreen 或企业策略可能显示“未知发布者”；请核对 Release 页面上的文件名和 SHA256，安装包属性中的 Publisher 应为 `XinXi Xu`。

## 联网与离线边界

- 本地项目管理、PDBQT 导入、Assisted 本地格式准备、Box 设置、Vina 对接、结果解析和 Markdown 报告导出不需要联网；
- 使用 RCSB PDB ID/关键词搜索、预览或下载受体，或使用 PubChem CID/名称搜索、预览或下载配体时需要联网；
- DockStart 运行时不会联网安装 Python、RDKit、Meeko 或其他科学包，也不会自动修改系统 PATH；
- 用户配置的兼容 preparation Python 优先于 Assisted 随附工具链。

## 当前支持

| 环节 | 支持格式或能力 |
| --- | --- |
| Basic 对接输入 | receptor `.pdbqt`、ligand `.pdbqt` |
| Assisted 受体原始输入 | `.pdb`、`.cif` |
| Assisted 配体原始输入 | `.sdf`、`.mol` |
| 在线获取 | RCSB PDB ID/关键词、PubChem CID/名称；显式选择后下载 |
| 结果输出 | docking PDBQT、`scores.csv`、Markdown 实验记录 |

当前不提供 MOL2/SMILES 自动准备、复杂受体修复、pocket prediction、PLIP/ProLIF 相互作用分析、Open Babel/MGLTools 内置、分子动力学、批量虚拟筛选或 AI 药效判断，也不修改 AutoDock Vina 算法或 scoring function。

## 发布门禁

- Python 测试：371 项通过（含 Basic NSIS 安装门禁测试）；
- 前端生产构建：4637 个模块完成转换；
- Cargo check、17 项测试与 clippy：通过；
- Basic 打包布局、两轮真实 PDBQT/Vina 流程、NSIS 安装后运行与卸载清理：通过；
- Assisted development / post-package / post-install 三道离线准备与对接门禁：通过，`publishable: true`；
- Basic 与 Assisted MSI 内容提取和离线运行验证：通过；
- 原始结构 GUI 人工流程：本机真实桌面端完成 PDB/SDF 导入、受体/配体转换和后续导航；
- 待完成：两个 MSI 在干净 Windows 设备上的真实安装/卸载；
- 待完成：用最终四个安装包在无开发环境依赖的 Windows 10/11 x64 设备上复验 GUI 主流程。

最后两项完成后，可把本 Release Candidate 标记为正式发布。

## 科学与许可证边界

自动生成 PDBQT 后仍需人工检查质子化、电荷、构象、缺失残基、水、金属、辅因子和链选择。对接箱体必须根据研究问题人工确认。

**Docking score 仅供结构结合趋势参考，不能替代实验验证，也不能证明真实结合、药效、安全性或临床价值。**

DockStart 自有代码采用 Apache-2.0；安装包内第三方组件遵循各自许可证。Meeko 0.7.1 以 LGPL-2.1 条款作为独立、可替换组件分发，详见安装包内 `resources/licenses/THIRD_PARTY_NOTICES.md`。许可证说明属于工程记录，不构成法律意见。
