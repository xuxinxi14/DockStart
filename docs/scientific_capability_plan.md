# DockStart 科学能力推进计划

本计划聚焦 AutoDock Vina 工作流质量，不扩展到分子动力学、药效预测或自动论文生成。

## 一级：标准小分子对接的可信基础（本轮完成）

- 结构准备预检
  - 配体：形式电荷、连接组分/盐、重原子数、可旋转键、立体标记、互变异构未知状态。
  - 受体：残基记录中断、链与模型、水、金属、非标准残基/辅因子、替代构象。
  - 所有结论均区分“文件事实”和“科学判断”，不自动声称质子化或结构处理正确。
- 评分函数
  - 支持 Vina 与 Vinardo。
  - AutoDock4 暂不开放；它需要 AutoGrid affinity maps，不能伪装成普通参数选项。
- 共晶姿势验证
  - 结果页可选择 SDF、MOL、PDB 或 PDBQT 参考配体。
  - 使用 RDKit 在同一受体坐标系内计算重原子、对称性修正 RMSD。
  - 化学连接或重原子数不一致时拒绝强行比较。
- 可复现记录
  - 运行输入、配置、Vina 二进制和参考配体记录 SHA256。
  - 保存评分函数、Box、参数、命令、系统信息、工具版本、stdout/stderr/log。
  - 历史报告从对应 run 快照生成，不读取后来被修改的项目参数。

发布门禁：结构预检、配置生成、快照报告、RMSD 错误路径与前端构建全部通过自动化测试；真实 1IEP 回归另作为安装后测试执行。

## 二级：常用对接模式（源码主链已完成，安装后回归待发布阶段执行）

二级能力包含批量虚拟筛选、有限柔性侧链和 Meeko 大环准备。这里的状态按以下口径记录，避免把“存在后端函数或界面组件”误写成“用户已经可以在安装版中完成流程”：

- **核心已实现（当前满足）**：参数和文件验证、命令规划、状态模型或结果解析已有代码和自动化测试。
- **运行主链已接入（当前满足）**：功能已经连接项目持久化、准备任务、Vina 执行、运行快照、结果页和错误恢复。
- **UI 已接入（当前满足）**：用户可从当前页面进入、保存并实际执行，而不只是存在未挂载组件。
- **安装后已验证（当前未满足）**：Basic 与 Assisted 安装包均使用真实 Meeko/Vina 完成标准样例，并验证重启、取消和恢复。

本轮已完成三个协议的项目持久化、后台执行、UI 接入和自动化测试；用户明确要求本轮不重新打包，因此 Basic/Assisted 安装后真实工具链回归仍是发布门禁，不能把“源码测试通过”表述成“安装包已验证”。

截至本文件更新，二级能力只能标记为“源码主链完成”，不能标记为“发布验证完成”或“安装后开箱即用已验证”。

### 2.1 批量虚拟筛选

目标是让多个已准备配体依次对接同一受体；它不是“多个配体同时进入一个口袋”的 simultaneous multiple-ligand docking。

**核心已实现**

- `screening.py` 提供项目内 PDBQT 输入校验、稳定排序队列、输入快照和 SHA256、逐配体尝试记录、失败重试、状态查询、取消请求、显式恢复及结构化中文错误。
- 状态独立保存在 `screening/screening.json`，不会为创建筛选任务而迁移旧版 `project.json`。
- 任务完成后写出全量 `screening_summary.csv` 和按结合能排序的 `screening_top_n.csv`。
- 有明确的配体数量、输入大小、CPU、搜索彻底程度、输出构象数和 Box 边长上限。
- 自动化测试覆盖成功队列、稳定顺序、重试、取消后恢复、资源越界、输入快照和中断恢复。

**运行主链与 UI 已接入**

- 对接工作台已接入配体快照导入、任务创建、后台运行、进度、失败重试、安全取消、恢复、归档、Top N 和 CSV 路径。
- 执行器当前是**串行队列**，UI 固定按受控单任务资源配置运行，不声称支持并行筛选。
- 当前批量筛选只支持刚性受体；项目启用有限柔性侧链时界面会阻断创建或恢复队列，不会静默退回旧的刚性输入。
- 取消请求在当前配体安全结束后生效，不会强制终止正在写结果的 Vina 进程。
- 只有 PDBQT 时无法可靠恢复键级，因此只输出 CSV。只有保存了对应原始配体拓扑并完成受控重建后，才能增加 SDF 汇总；不能从 PDBQT 猜键生成看似有效的 SDF。
- 批筛状态与单次 run 分开持久化，异常退出后只能显式恢复，不会静默重建或覆盖队列。

**发布验收待办**

1. Basic 与 Assisted 安装后分别用同一组配体验证排序、分数、异常退出后恢复和长路径/中文路径。
2. 增加至少一次 50 个以上配体的稳定性测试；资源使用不能阻塞 GUI 线程。

### 2.2 有限柔性侧链

目标是只放开少量口袋侧链，受体主链仍保持刚性。

**核心已实现**

- `advanced_protocols.py` 可从原始 PDB/mmCIF 验证 `chain:resnum[:icode]` 选择器，去重并限制残基数量；拒绝水、非聚合物、缺失残基和未解决替代构象。
- 不允许仅依据 PDBQT 猜测柔性残基来源；必须保留原始 PDB/mmCIF。
- 可构造安全参数数组形式的 Meeko 受体准备计划，并声明必须同时验证 rigid PDBQT、flex PDBQT 和 receptor JSON 三个输出。
- 可验证 Vina `--flex` 输入并生成对应参数片段。
- 自动化测试覆盖 PDB/mmCIF 选择器、插入码、替代构象、非法对象、输出规划和 Vina 参数。

**运行主链与 UI 已接入**

- 对接工作台已接入 `A:315` / `A:315:B` 精确残基选择、检查、后台准备和刚性/柔性切换。
- 准备任务使用项目内原始 PDB，原子发布 rigid PDBQT、flex PDBQT 和 receptor JSON 三件套；任一缺失或来源 SHA256 改变都不会激活。
- Vina run 会冻结 rigid/flex 输入，校验各自 SHA256，并仅在柔性模式加入一个 `--flex` 参数；报告记录模式、残基和准备编号。
- 旧项目缺少 `docking_protocol` 时仍解释为刚性受体，不迁移或改写既有受体文件。

**当前边界与发布验收待办**

1. 当前只接受原始 PDB；mmCIF 在没有经过审计的编号/altloc 桥接前明确拒绝。
2. 当前先提供精确文本选择；三维残基点选与基于柔性自由度的耗时估算属于后续交互增强。
3. 使用公开的柔性侧链示例在 Basic 与 Assisted 安装环境验证准备、运行、结果显示和失败恢复。

### 2.3 Meeko 大环配体准备

目标是显式管理 Meeko 的大环断环策略，并在结果导出时恢复可信的原始拓扑。

**核心已实现**

- `advanced_protocols.py` 可校验自动断环或保持刚性模式，以及最小环尺寸、双键惩罚、芳香 A 原子断环和等价/弦环策略。
- 可构造隔离 Python 方式的 `mk_prepare_ligand` 参数数组，不通过 shell 拼接命令。
- 可读取 Meeko PDBQT/metadata 中的 G* 伪原子、闭环锚点和断环记录，并在缺少嵌入拓扑时阻止不安全的 SDF 导出。
- 可构造 `mk_export` 计划；只有输入保留 `REMARK SMILES/SMILES IDX` 等足够拓扑时才允许恢复 SDF。
- 自动化测试覆盖参数边界、刚性/自动模式、断环证据、缺失拓扑拒绝和导出计划。

**运行主链与 UI 已接入**

- 配体准备页提供“标准准备（默认不变）/大环自动断环/大环保持刚性”三态；只有用户显式选择时才传入大环参数。
- 后台准备继续复用既有任务认领、raw SHA256、输出冲突检测和原子发布；协议、参数、Meeko 证据写入 preparation metadata。
- Vina run 只有在当前 ligand PDBQT 与 preparation 输出 SHA256 匹配时才继承大环协议，并冻结 `ligand_preparation.json`；手动替换后不会错误沿用旧标签。
- 结果页已接入 `mk_export` 拓扑 SDF 导出。缺少 `REMARK SMILES/SMILES IDX` 时以阻断错误结束，绝不根据距离猜测键级；每次导出使用新的 `sdf_NNN` 审计目录。
- 有限柔性侧链 run 当前不开放拓扑 SDF 导出：在 receptor JSON 与柔性残基映射尚未接入 `mk_export` 前明确阻断，PDBQT 原始结果保持可用。

**当前边界与发布验收待办**

1. 用至少一个可核对的大环样例验证准备前后连接关系、对接输出和导出 SDF；记录 Meeko 版本差异。
2. Basic 版若不含兼容的 Meeko / `mk_export`，应明确显示能力不可用；Assisted 版完成真实安装后回归。

### 二级测试与开发入口

从仓库根目录运行核心自动化测试：

```powershell
python -m unittest backend.tests.test_screening backend.tests.test_advanced_protocols backend.tests.test_flexible_receptor backend.tests.test_preparation_macrocycle backend.tests.test_result_export
```

命令行接口在 `backend` 目录中查看：

```powershell
python -m dockstart_core.screening --help
python -m dockstart_core.advanced_protocols --help
python -m dockstart_core.flexible_receptor --help
python -m dockstart_core.result_export --help
```

前端组件的类型与样式通过桌面前端构建检查：

```powershell
cd apps/desktop
npm run build
```

二级计划的源码完成定义：上述核心测试通过，三个协议完成项目、后台任务、运行快照、报告/导出和 UI 接入。发布完成仍要求 Basic/Assisted 安装环境通过真实工具链回归；本轮因明确不打包，发布门禁保留为待办。

这些源码测试验证的是状态机、文件门禁、命令参数、快照与 UI 构建，不替代真实 Meeko/Vina 的科学结果回归，也不证明安装包在其他设备上已经可用。

## 三级：AutoDock4 Maps 基础设施

- 生成 GPF、调用 AutoGrid4、验证 maps 完整性。
- 管理 grid points、spacing、受体/配体原子类型、参数文件与 AutoGrid 日志。
- 对接协议中单独提供 `AutoDock4 (maps)`，其分数不与 Vina/Vinardo 横向比较。

进入条件：完成 AutoGrid4 许可证与分发审查；建立标准非金属体系回归基准。

## 四级：AD4Zn 专用协议

- 识别 Zn 配位环境，生成 TZ 伪原子。
- 使用 AD4Zn 参数、GPF 与 AutoGrid maps。
- 将辅因子、水和配位残基处理设为显式人工确认项。

进入条件：三级 Maps 工作流稳定；1S63 等官方基准可复现。AD4Zn 只用于 Zn，不泛化到 Mg、Fe、Ca。

## 五级：实验性协议

- 水合对接。
- 多配体同时对接。

这些协议单独标记为实验性，不进入默认入门流程，也不与标准刚性对接混用参数页面。
