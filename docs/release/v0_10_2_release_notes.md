# DockStart v0.10.2 首次公开发布公告

DockStart v0.10.2 计划作为项目的第一个公开 Release。这个版本不增加新的 docking 算法，而是集中解决第一次使用时最容易中断的地方：安装包如何选择、原始结构从哪里获取、怎样把 PDB/CIF 与 SDF/MOL 准备成 PDBQT，以及完成一次对接后如何保留可追踪的结果记录。

发布平台：**Windows 10/11 x64**  
安装包 Author / Publisher：**XinXi Xu**  
核心对接引擎：**AutoDock Vina 1.2.7**

## 这次发布解决了什么

### 1. 项目入口按“我手上有什么文件”来选择

创建项目时不再要求用户先理解内部的 Basic/Assisted 命名，而是直接对应三种起点：

- **已有受体和配体 PDBQT**：直接进入最短对接流程；
- **只有 PDB/CIF 与 SDF/MOL**：进入原始结构准备与 PDBQT 转换流程；
- **先用示例体验**：复制内置小型项目，学习完整操作路径。

### 2. 在线获取原始结构不再是隐藏入口

原始结构流程中会持续提供明确的“在线搜索并下载”入口：

- 受体：按 RCSB PDB ID 或关键词搜索，可手动设置返回 1–20 个候选；
- 配体：按 PubChem CID 或名称搜索，可手动设置返回 1–20 个候选；
- 搜索结果不会默认下载第一项，可逐项加载不写入项目的原始结构 3D 预览；
- 只有明确点击“选择并准备”后，目标结构才会写入项目并开始 PDBQT 准备。

从搜索下载页、结构准备页或项目后续步骤都能重新找到入口，不需要记住第一次进入时点过的位置。

### 3. 明确告诉用户正在“准备并转换为 PDBQT”

Assisted 流程把容易误解的“检查输入”改成直接的受体/配体转换动作，并补充：

- 支持的输入格式；
- 转换前置条件；
- 后台任务进度；
- 失败后的中文恢复建议；
- 受体和配体都准备好后的下一步。

在线选择或本地导入 PDB/CIF、SDF/MOL 后会立即尝试生成 PDBQT；失败时保留 raw 文件和任务日志，用户可在“格式转换与 PDBQT 准备”中修复工具链或重试。CIF 受体由随附 Gemmi 转为受约束的中间 PDB，再交给 Meeko，修复了 Meeko CIF 路径隐式依赖未随包提供的 ProDy 所造成的转换失败。

PDB/SDF 到 PDBQT 并不只是修改扩展名。DockStart 会调用 Gemmi、RDKit、Meeko 完成最小准备，但结果仍需要人工科学检查。

### 4. 减少结构准备过程中的脚本启动等待

结构准备页面复用已有的工具链检测缓存，并把耗时转换放在后台任务中，减少切换页面或重复操作时冷启动 Python/RDKit/Meeko 所造成的界面停顿。发布前仍会在安装态和另一台设备上复验首次冷启动、重复进入页面和连续转换的表现。

### 5. 整理对接工作台与结果定位

- 左下角明确显示当前为 Basic 或 Assisted 安装 profile；
- “保存参数并重新检查”移动到 Vina 参数区下方，避免与启动操作分离；
- 对接工作台底部边框改为与内容区左右对齐；
- 结果构象列表增加“定位到当前配体”，用于在大受体中快速聚焦选中 pose。

### 6. 保留完整的本地对接闭环

两个 profile 都支持本地项目、对接箱体、Vina 参数、运行前检查、Vina 执行、构象与 score 查看、`scores.csv` 和 Markdown 实验记录导出。运行记录保存配置、命令、工具版本、stdout/stderr、时间和 SHA256 provenance。

## 该下载哪个安装包

DockStart 提供 Basic 与 Assisted 两个隔离的 Windows x64 profile，每个 profile 同时提供 NSIS `setup.exe` 和 MSI。

| 安装包 | 适合谁 | 能否从原始结构准备 PDBQT |
| --- | --- | --- |
| **Basic Stable** | 已经有 receptor.pdbqt 和 ligand.pdbqt | 否 |
| **Assisted Stable** | 只有受体 PDB/CIF 与配体 SDF/MOL | 是，可离线尝试 |

普通个人用户建议优先下载对应 profile 的 `setup.exe`；需要 MSI 部署时选择同 profile 的 `.msi`。

> Basic 与 Assisted 使用同一个应用身份，不能并行安装。切换 profile 前请先卸载当前版本。卸载应用不应删除位于独立工作目录中的用户项目，但发布前仍会执行安装/卸载回归。

## 四个 Windows x64 产物

以下数据来自提交 `fcbbd0548ab1ea4c3efdfedd3fb737ebbf962162` 的最终构建：

| 文件 | 大小 | SHA256 | 门禁状态 |
| --- | ---: | --- | --- |
| `DockStart_0.10.2_Basic_x64-setup.exe` | 17,789,525 B（16.97 MiB） | `7240a1b918ebfdf4053e37e0f26d0af12411adf62b7a5a3e43104bdcc25cee3f` | NSIS 真实安装、离线对接、卸载清理通过 |
| `DockStart_0.10.2_Basic_x64_en-US.msi` | 23,311,632 B（22.23 MiB） | `4de0858e59ddc5a2e9ea72fd316adc4a6fd69e6bebffd5e5eade3cc28db820f9` | MSI 内容提取与两轮离线对接通过；待干净机安装/卸载 |
| `DockStart_0.10.2_Assisted_x64-setup.exe` | 73,170,277 B（69.78 MiB） | `91d9d3c474768145d918925deb1f87bc5c9b526e322d2ddb6f25a85628e790a7` | development、post-package、真实安装后流程与卸载清理通过 |
| `DockStart_0.10.2_Assisted_x64_en-US.msi` | 113,120,476 B（107.88 MiB） | `4352962bed0be9ce09c4948fc4f77d1d770a6201e8985b8ab35a53870e760942` | MSI 内容提取及离线准备、对接、报告通过；待干净机安装/卸载 |

## 联网与离线边界

以下流程可以离线完成：

- 创建、打开和保存本地 DockStart 项目；
- 导入已有 PDBQT；
- Assisted 使用随附 RDKit/Meeko 从本地 PDB/CIF、SDF/MOL 尝试准备 PDBQT；
- 设置对接箱体与 Vina 参数；
- 运行 AutoDock Vina；
- 查看结果并导出 CSV/Markdown。

以下功能需要联网：

- 通过 RCSB PDB ID/关键词搜索、预览或下载受体；
- 通过 PubChem CID/名称搜索或下载配体。

DockStart 运行时不会联网安装 scientific packages，不会自动修改系统 PATH。Assisted 随附工具链是离线 fallback；如果用户在设置中选择了兼容的 preparation Python，该配置仍然优先。

## 支持格式

| 场景 | 当前支持 |
| --- | --- |
| 对接输入 | receptor PDBQT、ligand PDBQT |
| 受体原始结构 | PDB、CIF |
| 配体原始结构 | SDF、MOL |
| 在线结构来源 | RCSB PDB ID/关键词、PubChem CID/名称；显式选择后下载 |
| 结果 | PDBQT pose、CSV score、Markdown 实验记录 |

当前不支持 MOL2/SMILES 自动准备，也不承诺复杂受体修复、自动质子化/电荷判断、自动链选择或真实结合口袋识别。

## 随附工具与许可证

两个 profile 都随附 AutoDock Vina 与 DockStart 后端 runtime。Assisted 额外随附：

- CPython 3.11；
- RDKit 2026.3.3；
- Meeko 0.7.1；
- 经过清单固定和 SHA256 校验的必要依赖、许可证及对应源码获取材料。

Meeko 以独立、可替换的普通 Python 包分发，不会冻结进 DockStart 主可执行文件。DockStart 自有代码使用 Apache-2.0，第三方组件适用各自许可证。完整声明见安装包中的 `resources/licenses/THIRD_PARTY_NOTICES.md`。以上为工程合规说明，不构成法律意见。

## Windows 安装提示

本轮安装包计划由 `XinXi Xu` 作为元数据中的 Author / Publisher，但目前没有 Authenticode 数字签名。Windows SmartScreen 或组织策略可能显示“未知发布者”。下载后请：

1. 确认文件名与所需 profile 一致；
2. 对照本 Release 公告中的 SHA256；
3. 从 DockStart 官方 GitHub Release 页面获取安装包；
4. 不要同时安装 Basic 与 Assisted。

## 正式发布前验收状态

当前文档是 v0.10.2 发布候选公告。自动化与本机真实 GUI 验证已经完成，仍保留两项外部发布门禁：

- [x] Python 全量测试：352 项通过；
- [x] 前端生产构建：通过；
- [x] Cargo 测试 15 项与 clippy：通过；
- [x] Basic 打包布局中的真实 PDBQT/Vina/结果/报告流程：两轮运行通过；
- [x] Assisted development、post-package、post-install 三道真实流程门禁：全部通过，`publishable: true`；
- [x] Basic 与 Assisted NSIS 真实安装、离线运行与静默卸载：通过，无安装目录、运行时或卸载项残留；
- [x] Basic 与 Assisted MSI 内容提取及离线运行：通过；
- [x] 原始结构 GUI 流程：本机真实桌面端完成 PDB/SDF 导入、受体/配体 PDBQT 转换与下一步导航；
- [x] 四个安装包的大小、SHA256 与 `XinXi Xu` 元数据记录；
- [ ] Basic 与 Assisted MSI 在干净 Windows 设备上的真实安装/卸载；
- [ ] 用最终四个安装包在没有开发环境依赖的 Windows 10/11 x64 设备上复验 GUI 主流程。

在最后两项通过前，建议把这些文件称为 **v0.10.2 Release Candidate**；通过后可原样发布并创建 `v0.10.2` 标签。

## 已知边界

DockStart 不包含 Open Babel、MGLTools、PLIP 或 ProLIF，不提供相互作用分析、pocket prediction、分子动力学、批量虚拟筛选、AI 药效预测，也不修改 AutoDock Vina 算法或 scoring function。

自动准备结果仍需检查质子化、电荷、构象、缺失残基、水、金属、辅因子和链选择。对接箱体的几何定位不能代替真实口袋判断。

**Docking score 仅供结构结合趋势参考，不能替代实验验证，也不能证明真实结合、药效、安全性或临床价值。**
