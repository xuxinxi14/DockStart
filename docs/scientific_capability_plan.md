# DockStart 科学能力推进计划

本计划聚焦 AutoDock Vina 工作流质量，不扩展到分子动力学、药效预测或自动论文生成。

## 当前真实未完成任务（2026-07-30 审计）

本节是当前完成路径的权威摘要。下文按能力形成过程保留的旧版本号、打包状态和“待办”文字仅作历史记录；如与本节冲突，以本节和当前代码、测试、真实工具链证据为准。当前顺序固定为：先补科学闭环，再进行实际体验和 UI 优化，最后才处理打包与发布。

### P1：仍缺的科学闭环

1. **水合 AD4 的独立正向姿势恢复基准**：1UW6 固定参考链以及 2BYS/2ZJU 当前项目 API 真机链已经完成；后两者的参考 RMSD 分别为 `12.760 Å` 和 `6.422 Å`，只证明下载、准备、AutoGrid、Vina、后处理、结果和报告的工程闭环，明确不属于科学姿势恢复成功。仍需增加至少一个预先定义 RMSD 阈值并通过的独立共晶重对接体系，再扩展不同口袋水环境、Box/spacing 和工具版本。完成前保持 Experimental，也不能提供未定义的 dry/processed affinity。
2. **把已有真实 smoke 固化为可重放的外部验收**：`score_only`、双阶段 `local_only` 和高级参数已有真实 Vina 结果，但仍缺各自独立、不可静默跳过的公开输入 verifier；BACE_1 大环已有真实项目 smoke，但仍需覆盖对接后拓扑重建/闭环复核和明确不支持案例；Vina/Vinardo 预计算 maps 已完成真实生成与复用，真实进程异常、竞争和重试证据仍需从 mock 可靠性矩阵提升为可重放验收。
3. **批量规模证据**：当前串行批筛的科学语义、50 配体压力、失败隔离、取消和恢复已经完成；仍需在 100/500 项中建立可重复的耗时、峰值内存和恢复一致性基准，再决定是否立项并行执行。单项取消若继续作为产品承诺，应独立实现和验收，不能用整队列取消替代。

### P2：已完成源码与真实工具链闭环，但仍保持实验成熟度

1. **有限柔性受体**：PDB 与 mmCIF、残基编号、插入码、altloc、严格身份映射以及配体/侧链独立运动分析已经接入；1H4W 真实准备、Vina、结果与报告验收已完成。
2. **多配体共同对接**：5X72 真实链和 4DM3 多 seed 验收已经覆盖成员顺序、总自由度/可旋转键、Box、联合评分、输出 Mode 前缀、恢复与篡改门禁；仍保持 Experimental，不把联合分数拆成成员 affinity。
3. **AD4Zn beta**：1S63、2OI0、1R1J 已完成 TZ → AutoGrid4 → maps → Vina → 结果/报告真机链；无 TZ、多金属、非 Zn、参数篡改、工具失败和恢复路径已经 fail closed。样本量仍不足以升为 Stable。
4. **水合 AD4 工程链**：当前项目 API 的 2BYS/2ZJU 真机验收、大网格、并发、终止/恢复、篡改和结果序列化门禁已经通过；其剩余问题是上面的正向科学基准，不再是“项目级 AutoGrid 主链尚未实现”。

### P3：明确延期或可选

- `randomize_only`；
- 自定义评分权重；
- AD4 批量 maps；
- AD4 柔性受体；
- Python API / 协议插件；
- 批量并行执行（先完成 100/500 项资源基准）；
- 只有 schema v1 无法安全表达已批准能力时才迁移整个项目 schema，不把 schema v2 当作独立功能。

### 已完成，不应重复列为待办

- 标准刚性单配体 Vina/Vinardo、`score_only`、双阶段 `local_only`；
- 七项高级 Vina 参数及运行时能力门禁；
- 标准 AutoGrid4/AD4 maps 与真实 1DWD；
- 批量核心队列、失败隔离、重试、取消/恢复、Top N、结果工作区、历史比较和校验 ZIP；
- 批量配体原始拓扑与准备事实闭环：每条 SDF/MOL record 以不可变字节冻结，形式电荷、重原子数、严格可旋转键、片段和大环事实由该拓扑计算；失败清单、审计重试、候选 revision、准备证据和大环 `review_required` 门禁已经接入；结果 SDF 只通过真实 Meeko 拓扑映射导出并用 RDKit 逐构象核对规范重原子图，绝不从 PDBQT 坐标猜键；现代归档比较使用 PDBQT SHA256 与 canonical topology SHA256 的复合身份，旧 PDBQT-only 归档保持只读兼容；
- PDB 有限柔性主链；
- Vina/Vinardo 预计算 maps 科学核心；
- 有限柔性 1H4W、多配体 5X72/4DM3、AD4Zn 1S63/2OI0/1R1J 的真实工具链主链与失败门禁；
- 水合 1UW6 固定参考链，以及 2BYS/2ZJU 当前项目 API 工程链；后两者不宣称姿势恢复成功；
- 正式 Meeko 大环 schema v2：候选断环、人工确认、刚性选择、显式氢/原子映射、键拓扑、工具版本、不可变合同、fail-closed 发布、run 快照、报告和 3D 候选定位；已用 Assisted RDKit 2026.03.3、Meeko 0.7.1、Vina 1.2.7 对 BACE_1 完成真实项目级 smoke。该项完成不代表已经打包或发布。

批量闭环已用随附 Python 3.11.15、RDKit 2026.03.3 和 Meeko 0.7.1 完成真实工具链 smoke：普通乙醇记录进入 ready，环庚烷按七元环事实进入不可自动重试的 `review_required`；真实 `mk_export` 生成的 SDF 由同一随附 RDKit 重新读取，独立 DockStart 属性、零值、Meeko 原属性、规范图 SHA256、pose 数量及工具链快照均通过复核。2026-07-30 当前工作树的完整回归为后端 `1150/1150`、前端 `86/86`、Rust `32/32`，TypeScript/Vite 生产构建通过；水合专项为 `94/94`。该证据描述当前源码，不代表已经打包或公开发布。

## 一级：标准小分子对接的可信基础（本轮完成）

- 结构准备预检
  - 配体：形式电荷、连接组分/盐、重原子数、可旋转键、立体标记、互变异构未知状态。
  - 受体：残基记录中断、链与模型、水、金属、非标准残基/辅因子、替代构象。
  - 所有结论均区分“文件事实”和“科学判断”，不自动声称质子化或结构处理正确。
- 评分函数
  - 支持 Vina 与 Vinardo。
  - AutoDock4 通过独立的 affinity maps 协议开放；它依赖经过校验的 AutoGrid4 maps，不能伪装成普通评分参数。
- 共晶姿势验证
  - 结果页可选择 SDF、MOL、PDB 或 PDBQT 参考配体。
  - 使用 RDKit 在同一受体坐标系内计算重原子、对称性修正 RMSD。
  - 化学连接或重原子数不一致时拒绝强行比较。
- 可复现记录
  - 运行输入、配置、Vina 二进制和参考配体记录 SHA256。
  - 保存评分函数、Box、参数、命令、系统信息、工具版本、stdout/stderr/log。
  - 历史报告从对应 run 快照生成，不读取后来被修改的项目参数。

发布门禁：结构预检、配置生成、快照报告、RMSD 错误路径与前端构建全部通过自动化测试；真实 1IEP 回归另作为安装后测试执行。

### 一级补充：现有姿势评分与局部优化（源码定量比较闭环已接入，尚未发布）

本轮在标准单次运行主链上增加 AutoDock Vina 原生 `score_only` 与 `local_only` 运行模式。它们是姿势评价工具，不是新的评分算法，也不能与全局对接的多构象搜索混为一谈。

**共同数据与兼容边界**

- 项目在现有 schema v1 的兼容扩展字段中保存 `docking_protocol.run_mode` 与 `docking_protocol.autobox`；每次运行在 metadata 和配置快照中冻结实际模式。
- 新建项目页把“文件来源”和“本次任务”分开选择；帮助页与项目总览提供当前姿势评分、局部优化的直接入口，不另建一套运行或结果页面。
- `score_only` / `local_only` 启动前必须由用户在同场 3D 视图中确认配体已位于受体中的待评价位置并使用同一坐标系。确认记录保存为 `docking_protocol.pose_input_attestation`，绑定本次运行实际使用的刚性受体、可选柔性侧链与配体 PDBQT 的 SHA256；任一输入替换或柔性受体重建后自动失效。该记录只证明用户完成了复核，不代表 DockStart 已科学验证姿势。
- 评价任务不开放“RCSB 受体 + 独立 PubChem 配体”的在线组合入口；PubChem 构象没有天然处在所选受体坐标系中。可使用已经共同定位的 PDBQT，或导入来自同一坐标系并在转换后重新复核的本地原始结构。
- 准备 run 时把确认记录冻结进 metadata；执行 prepared run 前再次核对确认格式，并要求其中刚性受体、可选柔性侧链与配体 SHA256 与本次不可变输入快照逐项一致。手工移除或改写确认后不会启动 Vina。
- 旧项目没有上述字段时固定按 `dock` 和 `autobox = false` 解释，不迁移、不重写，也不改变原有标准对接行为。
- 旧项目已经保存评价模式但没有姿势确认时，已完成的历史结果仍可读取；再次准备新 run 前必须完成确认。
- Vina/Vinardo 可继续使用项目 Box，也可由用户显式启用 autobox。
- AutoDock4 maps 已由 maps 网格定义评价范围，因此该协议下强制关闭 autobox，不把两种范围来源混合使用。
- 批量筛选仍固定运行全局对接，不继承单次任务选择的 `score_only` 或 `local_only`。

**`score_only`**

- 只计算输入受体—配体姿势的能量分项，不执行全局搜索。
- 成功运行不要求也不生成新的输出 PDBQT；Viewer 使用本次运行冻结的输入配体快照。
- 结果写入独立的 `evaluation.json`，页面与报告显示姿势评分和可解析的能量分项。
- 不生成 `scores.csv`、pose 排名、虚假的“Mode 1”或 RMSD。

**`local_only`**

- 只从输入姿势附近进行局部优化，不代表已经搜索整个 Box，也不能解释为全局最佳姿势。
- 新准备的 run 使用同一份不可变 receptor、ligand、flex/maps 与配置快照，先执行独立 `score_only` 记录输入姿势基线，再执行 `local_only`；两份 stdout、stderr 和 log 分开保存，不能拼接日志推断基线。
- 优化结构保存为本次运行的 `optimized.pdbqt`，输入与优化后评分、能量分项、差值定义和分阶段记录保存到 `evaluation.json`。
- 评分差固定定义为“优化后－输入”。负值只表示在同一 Vina 评分协议下数值降低，不能写成真实结合自由能或药效改善。
- 按可证明的原子身份比较冻结输入与 `optimized.pdbqt`：优先使用 Meeko SMILES 原子索引，否则严格校验 PDBQT serial 与完整身份；不按坐标近邻猜测。
- 报告同一受体坐标系下、**不做刚体对齐**的重原子 RMSD、平均/最大重原子位移和几何质心位移。该数值包含整体平移与旋转，不是共晶参考 RMSD，也不设置“好/坏”阈值。
- 现代双阶段 run 的结果页默认在同一个 3D 场景中叠合本次 run 冻结的受体、输入姿势与优化后姿势，也可只显示其中一个姿势。叠合直接使用两份原始坐标，不做额外刚体对齐；Viewer 在读取前核对冻结输入和优化输出的 SHA256，不读取后来被替换的项目当前受体、配体或 raw 拓扑。
- 旧的 `local_only` run 没有双阶段执行计划时继续按原单阶段记录读取，明确显示“未记录输入基线”，不补零、不伪造差值。

**高级 Vina 参数与运行时能力门禁**

- 单次运行已接入 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine`、`force_even_voxels` 和可空的 `unbound_energy`；串行批量全局对接继承前六项适用参数。前四项默认值分别为 `0`、`1 Å`、`0.375 Å` 和 `1`，两个专家开关默认关闭，`unbound_energy` 默认不显式指定。旧项目缺字段时保持 Vina 默认行为。
- `max_evals` 与 `min_rmsd` 只写入全局对接配置；`score_only` 与 `local_only` 不显示或写入这两个全局搜索参数。
- `spacing` 只用于现场计算 Vina/Vinardo 网格；AutoDock4 maps 使用其预计算网格，不写入项目的 Vina `spacing`。
- GUI 只开放 `verbosity = 1/2`。详细日志下，Vina 1.2.7 的 `local_only` 会输出优化前后两个能量块；解析器只在存在明确局部优化边界时选择最后一块，其他重复块继续拒绝。
- `no_refine` 让 Vina 在最终优化和评分阶段继续使用网格近似，而不切换为显式受体原子。该选项会改变评分路径，只允许与同一设置、同一 Vina 版本和同一输入的运行比较；为避开上游已知能量计算问题，DockStart 至少要求 Vina 1.2.4。
- `force_even_voxels` 强制每个轴使用偶数个体素区间。取整时实际网格边界可能比用户输入扩大一个 `spacing`；使用显式项目 Box 时，运行前资源估算会记录被调整的轴与实际点数，开关本身始终进入单次 run 或批量队列的冻结快照。autobox 的最终范围以 Vina 日志为准。
- `unbound_energy` 只在 Vina/Vinardo、刚性受体、单配体 `score_only` 中开放；留空与显式 `0` 分开保存，只接受有限数值。它改变未结合体系的评分参考，不是实验结合自由能；Vina 会在构象无关变换前使用该项，因此对含可旋转键的配体，参数变化不应被解释为最终分值必然按同数值平移。全局对接、`local_only` 双阶段比较、柔性受体、AutoDock4 maps 和批量筛选均不写入该项。
- Vina/Vinardo 启用任一需要门禁的选项前，DockStart 会读取当前可执行文件的 `--help_advanced`，按独立选项行精确确认能力，并结合最低安全版本门禁；评价模式的 `autobox` 要求稳定版 Vina 1.2.3 或更高版本，`unbound_energy` 最低门槛按 SemVer 与稳定版 Vina 1.2.4 比较，因此 `1.2.4-rc1` 低于后者门槛，而 `1.2.4+build.7` 不因构建元数据降级。版本无法解析、探测失败或声明缺失时不猜测支持，也不会启动 Vina；检测结果、版本和帮助摘要 SHA256 会进入准备记录。对新生成的 `score_only` run，结果分析先核对 metadata 记录的日志 SHA256；显式 `unbound_energy` 还要求日志第 (1)～(4) 项编号正确且均为有限数值，第 (4) 项与冻结值一致，并验证日志总评分等于 `(1) + (2) + (3) - (4)`。生成后的 `evaluation.json` 会记录独立 SHA256，结果加载和报告生成都会复核，并严格拒绝布尔值或非有限值伪装成显式参考能量。任一检查失败时均拒绝把结果标记为显式未结合态参考评分；缺少现代审计字段的历史 run 仍按兼容边界读取，但只要快照声明了显式 `unbound_energy` 就必须具备可信日志哈希。
- 运行前按 Box、spacing 和可移动原子类型估算网格内存：超过 512 MiB 提示，超过 2 GiB 阻断，避免极小 spacing 与大 Box 组合直接耗尽内存。
- 单次运行的七项参数进入项目配置、生成配置、不可变 run 快照与报告。串行批量筛选冻结 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine` 和 `force_even_voxels`，并把它们写入每个配体的独立 config、整批报告和归档协议指纹；`unbound_energy` 不进入批量状态或 config。
- 执行前还会再次读取冻结配置并核对当前 Vina，避免准备后替换二进制而绕过能力门禁。批量队列启用 `no_refine` 或 `force_even_voxels` 时同样要求当前运行时明确声明支持，并在每次开始或恢复队列前统一复核。旧调用方只更新基础字段时保留已经保存的高级值，未知 `vina` 子字段继续往返保留。

**尚未纳入本轮**

- `randomize_only`；
- 自定义评分权重等其余专家参数；
- 轨迹动画或对称性重映射；
- Basic/Assisted 安装包真实 Vina 回归和发布验收。

源码树内置 Vina 1.2.7 已完成端到端回归：`score_only` 不生成输出 PDBQT；新的 `local_only` 在同一 run 中依次记录输入评分和局部优化，真实样例得到 `36.334 → -0.041 kcal/mol`，差值为 `-36.375 kcal/mol`，并完成几何比较、同场 3D 叠合与报告生成。非默认 `max_evals = 500`、`min_rmsd = 0.5 Å`、`spacing = 0.5 Å`、`verbosity = 2` 也已分别通过真实 `dock`、`score_only` 与 `local_only` smoke；仅适用全局搜索的两项不会进入评价模式配置。`no_refine`、`force_even_voxels` 与 `unbound_energy` 已通过 Vina 1.2.7 高级帮助能力探测和真实命令 smoke；刚性无扭转样例显式设置 `unbound_energy = 5` 时，日志第 (4) 项为 `5.000 kcal/mol`，评分由默认参考下的 `36.334` 变为 `31.334 kcal/mol`。该一比一变化只描述此无扭转样例，不能外推到含可旋转键体系。自动化测试还覆盖显式 `0`、负值、非有限值、重复配置、同核心号预发布版本关闭门禁、帮助声明门禁、姿势确认缺失或哈希失效阻断、确认记录进入 run 快照、prepared run 执行前再次核对确认与 immutable 输入哈希、现代 `score_only` 日志 SHA256 缺失或不一致拒绝、第 (1)～(4) 项号或能量平衡异常拒绝、第 (4) 项与运行快照不一致拒绝、`evaluation.json` 篡改或非法显式值拒绝、非适用协议休眠、基线失败不启动第二阶段、两阶段间取消、旧 run 单阶段兼容、旧柔性模式字段兼容、严格原子映射、高级值旧调用兼容和偶数体素网格资源估算。本节仍只记录源码能力，不修改 DockStart 版本号或发布状态；Basic/Assisted 安装态验证仍是发布门禁。

## 二级：常用对接模式（源码主链与真实工具链回归已完成）

二级能力包含批量虚拟筛选、有限柔性侧链和 Meeko 大环准备。这里的状态按以下口径记录，避免把“存在后端函数或界面组件”误写成“用户已经可以在安装版中完成流程”：

- **核心已实现（当前满足）**：参数和文件验证、命令规划、状态模型或结果解析已有代码和自动化测试。
- **运行主链已接入（当前满足）**：功能已经连接项目持久化、准备任务、Vina 执行、运行快照、结果页和错误恢复。
- **UI 已接入（当前满足）**：用户可从当前页面进入、保存并实际执行，而不只是存在未挂载组件。
- **真实工具链回归（2026-07-27 已满足）**：验收证据已覆盖批筛失败隔离/取消恢复/50 配体、1FPU 柔性对接、BACE_1 大环和标准 AutoGrid4/AD4；这些证据不等于干净账户安装、系统重启和卸载生命周期验收。

项目持久化、后台执行、UI 接入、自动化测试和上述真实工具链回归已经存在。当前新增的评价模式、高级参数和批量结果工作区仍只在当前工作树中，本轮没有重新打包；项目尚无公开 Release，因此不能把这些新增量反向写入历史安装包。

截至本文件更新，二级既有协议可以标记为“真实工具链回归完成”；正式发布仍需区分 post-package 门禁与干净 Windows 安装/升级/卸载证据。

### 2.1 批量虚拟筛选

目标是让多个已准备配体依次对接同一受体；它不是“多个配体同时进入一个口袋”的 simultaneous multiple-ligand docking。

**核心已实现**

- `screening.py` 提供项目内 PDBQT 输入校验、稳定排序队列、输入快照和 SHA256、逐配体尝试记录、失败重试、状态查询、取消请求、显式恢复及结构化中文错误。
- 配体库入口支持多文件与目录递归导入。多分子 SDF 保留包括坏记录在内的 1-based record index；每条 SDF/MOL record 的精确原始字节冻结到不可变 import 目录，隔离 worker 逐条计算形式电荷、重原子数、严格可旋转键、片段、最大环和 canonical graph 身份。单条化学错误不阻断其他记录。准备后 PDBQT 与 canonical topology 组成逻辑去重身份，因此不同原始拓扑即使偶然得到相同 PDBQT 字节也不会被错误合并。
- 每次导入保存 import/candidate/record 身份、revision SHA256、worker script/manifest、Python/RDKit/Meeko 版本、准备尝试和独立 JSON/CSV 失败清单。失败项只能从项目内冻结 record 审计重试；外部源文件被移动后仍可复现。检测到七元及以上大环时固定进入不可自动重试的 `review_required`，不会绕过正式单配体大环审查。
- 状态独立保存在 `screening/screening.json`，不会为创建筛选任务而迁移旧版 `project.json`。
- 任务完成后写出全量 `screening_summary.csv` 和按结合能排序的 `screening_top_n.csv`；终态任务可另生成带工具、输入、Box、基础参数、六项适用高级参数、Top N、全量结果和科学边界的 `screening_report.md`。
- 成功输出记录文件大小与 SHA256；逐配体 Viewer 只接受当前 item 成功 attempt 下的 `out.pdbqt`，同时读取本次筛选冻结的受体。历史 Viewer 还要求合法 `archive_id`，并把受体、item 与 attempt 严格限定在所选归档中，只开放成功配体的 Mode 1。
- 归档索引会校验归档标识、清单、状态文件、状态 SHA256 和目录边界。损坏归档不会从列表消失，但其详情和结构读取会被阻止；旧归档缺少输出哈希时保持可读，并明确标为未完全验证。
- 双归档比较要求恰好两个不同且完整性校验有效的归档，按选择顺序固定为基线与对照；比较前实际核对两批冻结受体和全部冻结配体输入的文件大小与 SHA256，且全程不改写项目或归档。
- 现代逐配体身份使用冻结 PDBQT SHA256 与 canonical topology SHA256 的复合身份；比较前实际核对原始 topology 文件、record SHA256、化学事实、准备证据及 import/candidate/record 身份。相同 PDBQT、不同原始拓扑不会被误配。旧归档或明确 PDBQT-only 条目继续按冻结 PDBQT SHA256 只读匹配；同一归档内重复身份标记为 `ambiguous`，不使用名称或顺序消除歧义。协议指纹覆盖受体 SHA256、评分函数、Box 六项、Vina 版本与二进制 SHA256，基础参数 `exhaustiveness`、`num_modes`、`energy_range`、`cpu`、`seed`，以及 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine`、`force_even_voxels`。
- 有明确的配体数量、重试次数、Top N、单配体与总输入大小、CPU、搜索彻底程度、输出构象数、Box 边长和高级数值参数边界；全部九项 `resource_limits` 在新队列中冻结并记录 SHA256，开始、恢复、归档详情与比较时重新核对，且不能放宽超过应用硬上限。旧记录缺资源字段或哈希时按默认上限兼容并明确标为推断或未完全验证。批量模式不接受 `unbound_energy`。
- 自动化测试覆盖成功队列、稳定顺序、重试、取消后恢复、资源越界、输入快照和中断恢复。

**运行主链与 UI 已接入**

- 对接工作台已接入配体库导入预览，显示记录、可用、重复、失败和已选数量；重复与失败项不能勾选，创建队列只接收用户选择的唯一可用 PDBQT。任务创建、基础与适用高级参数冻结、后台运行、进度、失败重试、安全取消、恢复、归档、Top N、CSV 与 Markdown 路径均已接入。
- 结果工作区展示全部条目，支持文本搜索、状态筛选、评分/顺序/名称/状态排序、Top N 切换与分页；结构内容只在用户打开成功项时按需加载，不把整个配体库一次性送入前端内存。
- 历史归档入口展示归档列表和只读结果详情，可查看报告状态并重新打开成功配体的 Mode 1。归档不提供恢复活动队列、编辑参数、重试或取消操作。
- 历史列表可选择两个有效归档进行严格只读比较。只有协议指纹相同、配体身份在两侧唯一、两侧条目均成功且评分与排名为有效数值时，才计算评分和排名的 `Δ = 对照－基线`；协议不同仍并排展示记录，但不生成差值。
- 有效历史归档可导出为带根清单的单个 ZIP。导出前复核源归档、`attempt.json` 与冻结 attempt 记录，以及构象、完整汇总、Top N 和 Markdown 实验记录的历史身份；包内逐文件记录大小与 SHA256，并保存 payload 树哈希；写入后重新检查 ZIP CRC、成员集合和逐文件哈希。现代记录生成这些结果时同步保存历史大小与 SHA256；旧的可安全读取归档仍可导出，但缺少凭据的产物会保留“部分验证/兼容读取”警告，不会用导出时新计算的哈希升级成现代完整验证。
- 归档 ZIP 是只读、可移动的实验记录，不是项目备份或运行环境：不包含 Vina 二进制、活动队列、项目当前状态和 staging，不能直接导入或恢复运行。既有 JSON 可能包含绝对本机路径，导出不执行匿名化；哈希也不构成数字签名或历史真实性证明。
- 执行器当前是**串行队列**，UI 固定按受控单任务资源配置运行，不声称支持并行筛选。每个配体使用队列冻结的六项适用高级参数生成独立 config；专家开关必须通过当前 Vina 能力门禁，并在开始或恢复队列时统一复核。每次 attempt 会把冻结受体和当前配体按实际字节写入独立目录，并在 Vina 启动前、返回后分别核对受体、配体、配置和当前 Vina 二进制的大小与 SHA256；全部一致才允许成功，证据写入 `attempt.json`。归档详情和双归档比较还会复核归档中的 attempt 输入与配置；Vina 二进制不复制进归档，只保留冻结的版本、大小和 SHA256 证据。
- 当前批量筛选只支持刚性受体；项目启用有限柔性侧链时界面会阻断创建或恢复队列，不会静默退回旧的刚性输入。
- 取消请求在当前配体安全结束后生效，不会强制终止正在写结果的 Vina 进程。
- 只有 PDBQT 时无法可靠恢复键级，因此只输出 CSV。存在已验证冻结原始拓扑的成功条目会通过真实 Meeko `mk_export` 恢复姿势 SDF，再用隔离 RDKit 验证每个构象的 canonical heavy-atom graph 与冻结事实一致；通过的构象进入聚合 SDF、pose map 和验证 manifest，缺拓扑或不一致项进入显式失败覆盖。聚合记录保存 item/candidate/record/topology/result 身份、pose index 和评分，并保留零值；工具链快照记录 Python 可执行文件 SHA256/大小及 Python、RDKit、Meeko 版本。再次读取已有结果会复核聚合文件、清单、pose map 和全部审计文件，不从 PDBQT 猜键。
- 批筛状态与单次 run 分开持久化，异常退出后只能显式恢复，不会静默重建或覆盖队列。

**科学任务完成后的体验与发布待办**

1. 科学证据在冻结源码上收口后，再用真实项目复核完整结果表、逐配体 Viewer、报告生成与归档前操作顺序，并补暗色/亮色及中文长路径截图；UI 稳定后才重新打包并决定首个公开 Release 版本号。
2. 已完成 50 配体真实 Vina 压力、失败隔离及取消恢复；下一规模门禁应在性能测量后选择 100 或 500 项，不能在现有上限 500 的情况下继续写“1000 项已支持”。
3. 归档只读浏览、双归档严格只读比较和单归档可校验 ZIP 导出已进入当前源码增量；归档删除、ZIP 导入/恢复和多个构象并排查看仍需单独设计。
4. 原始 record 冻结、化学事实、失败重试、失败清单、大环隔离和拓扑验证 SDF 已完成；后续批量并发、单项强制取消、归档删除和 ZIP 导入/恢复属于产品增强，不再作为当前科学闭环缺口。本批次不修改版本号，也不重新打包。

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

**当前成熟度与后续体验边界**

1. 原始 PDB 与 mmCIF 均已接入。mmCIF 使用经过审计的残基身份、编号、插入码、altloc 选择和中间 PDB 桥接，不再属于“明确拒绝”的未实现能力。
2. 精确文本选择和全屏三维残基点选均已接入；1H4W 已覆盖 mmCIF、插入码、altloc、严格身份映射、真实准备、Vina、配体/侧链独立运动分析、结果和报告。后续扩大更复杂结构样本属于成熟度工作，不再写成 mmCIF 主链缺失。
3. 1FPU 的 PDB 柔性案例同样已经完成异常残基审阅门禁、准备、真实柔性 Vina 对接和 CLI 一致性复验。干净安装环境、逐页体验与 GUI 截图统一留到科学闭环完成后的体验和发布阶段。

### 2.3 Meeko 大环配体准备

目标是把 Meeko 大环断环从隐式自动行为改成“原始结构分析 → 候选断环审查 → 用户确认 → 精确执行 → 运行与报告追溯”的正式协议。

**核心已实现**

- `macrocycle.py` 从项目内 SDF/MOL 原始字节建立 schema v2 审查记录，冻结原始输入、完整原子表、规范键拓扑、显式氢策略、候选集合、RDKit/Meeko 版本及各层 SHA256；Windows 中文/Unicode 路径不再依赖工具直接打开文件。
- 分析阶段使用 `rdkit_add_hs_preserve_source_indices_v1`：源原子索引保持不变，新增氢只追加到原子表；候选 ID 同时绑定原子、键拓扑和协议选项，不能由前端伪造断环键。
- 标准 Meeko 配体准备检测到 RDKit 感知的七元及以上环或 Meeko 已移除环键时，在 writer 和输出发布前返回 `MACROCYCLE_REVIEW_REQUIRED`，不再静默自动断环。
- 新任务不接受旧 `auto` / `rigid` 模式。用户只能确认服务端候选，或在同一份正式审查记录上明确选择保持刚性；确认记录和执行合同均不可变并由 SHA256 绑定。
- 隔离 worker 重新核对 RDKit/Meeko 版本、显式氢映射、完整键拓扑、候选集合和候选 ID，再通过 Meeko API 精确设置 `delete_ring_bonds` 与 `glue_pseudo_atoms`。预期断环、实际断环、G* 数量、输出拓扑或文件哈希任一不一致都会 fail closed，不发布候选文件。
- 证据门禁完成后仍会以同一次读取的候选字节执行最终原子发布，关闭“校验后替换候选文件”的 TOCTOU 窗口；旧的 prepared 文件在失败时保持不变。

**运行主链与 UI 已接入**

- 配体准备页显示审查设置、服务端候选、推荐项、逐键一基编号和原始配体 3D 候选定位；用户确认候选或保持刚性后，准备请求只传 `review_id` 与 `confirmation_sha256`。
- preparation metadata 冻结审查、确认、合同、worker 证据、输入/输出、原子映射、键拓扑及工具版本；Vina run 会重新交叉核对当前 ligand PDBQT 和全部正式证据，只把完整匹配的协议标记为 `formal_reviewed`。旧记录仍可读取，但只能标记为 `legacy_partial`。
- Markdown 报告显示候选 ID、一基编号断环键、G* 数量、显式氢策略、键拓扑/合同/证据/冻结输入哈希和工具版本，并明确断环、质子化、电荷与构象选择仍需人工科学判断。
- 结果 SDF 恢复继续要求可信嵌入拓扑；任何路径都禁止根据 PDBQT 原子距离猜测键级。

**真实工具链证据与当前边界**

1. Assisted Python 3.11.15、RDKit 2026.03.3、Meeko 0.7.1 与 Vina 1.2.7 已对 BACE_1 完成 schema v2 项目级 smoke：生成 7 个候选，确认 `candidate_72113262ddb85177`，预期/实际断开 C3—C4，G* 为 2；`run_001` 正常结束并把正式证据写入 run 与报告。该低 `exhaustiveness` 运行只验证工具链，不是科学复现实验。
2. 正式候选选择仍只在单配体准备页完成；批量 worker 已在 Meeko writer 发布前识别七元及以上大环或实际移除环键，并转为不可自动重试的 `review_required`。批量入口不会静默采用自动断环；用户需先完成正式单配体审查，再将受审查的 PDBQT 作为显式输入。
3. Basic 版缺少兼容 Meeko/RDKit 时应明确显示能力不可用。干净安装、升级、卸载和截图属于科学能力完成后的体验与发布阶段，不影响当前源码闭环判断。

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

状态：**历史本地 v0.12.0 源码/打包检查点已完成；项目从未公开发布该版本的 Release**。

- 已实现独立 AutoGrid4 adapter：配置路径或 PATH 检测、版本识别、安全参数数组调用、stdout/stderr/GLG 留档和结构化错误。
- 已实现 GPF 生成与参数管理：grid center、偶数 grid points、spacing、受体/配体原子类型和可选参数文件。
- 已实现 maps 生成、外部导入、manifest、文件 SHA256、受体 SHA256 绑定、配体原子类型校验、Box 一致性校验和失效阻断。
- 已实现独立 `AutoDock4（maps）` 协议；它不出现在 Vina/Vinardo 评分下拉框中，运行使用 `--maps` 与 `--scoring ad4`。
- 每次运行会把 maps 与 manifest 复制到 `runs/{run_id}/inputs/maps/`，执行前再次校验不可变路径和 SHA256。
- AD4 全局对接项目汇总分别写入 `results/ad4_scores.csv` 与 `reports/ad4_docking_report.md`；AD4 评价项目报告分别写入 `reports/ad4_score_only_report.md` 与 `reports/ad4_local_only_report.md`，不覆盖 Vina/Vinardo 文件；结果页与报告均明确禁止协议间直接比较。
- GUI 已提供协议切换、网格参数、原子类型、参数文件、maps 生成/导入、工具状态、manifest 和失效原因。

许可证与分发结论：

- AutoGrid4 4.2.6 由上游按 GNU GPL 分发；
- 历史本地 v0.12.0 Basic/Assisted 打包检查点都**不内置 AutoGrid4**；
- 用户自行安装后，可在设置页配置 `autogrid4.exe`，或由 PATH 自动检测；
- 安装包继续内置 Apache-2.0 的 AutoDock Vina 1.2.7，用于读取 AD4 maps 并执行对接。

标准非金属回归：

- 2026-07-26 使用 Scripps 官方 AutoDock 4.2.6 `1dwd` 示例；
- AutoGrid4 4.2.6 成功重新生成 60 × 60 × 60、0.375 Å 的完整 maps；
- DockStart 新工作流完成 `ad4_001 → run_001 → results/ad4_scores.csv → reports/ad4_docking_report.md`；
- 随附 Vina 1.2.7 以 `ad4` 评分完成运行，固定 seed 12345 下最佳评分为 -11.55 kcal/mol。

历史 v0.12.0 打包基线的标准 AD4 工作流边界只开放非金属、刚性受体、单配体协议。后续源码中的 Zn 专用能力进入四级 AD4Zn beta；水合 AD4 已在当前工作树中形成独立 Experimental 闭环，但不属于旧安装包。批量 AD4 maps、柔性受体 AD4，以及水合协议与其他高级协议的组合仍未开放。

### 三级补充：Vina / Vinardo 预计算 maps 复用（当前源码增量）

状态：**源码闭环已接入，尚未分配发布版本或重新打包**。

- 使用已经随 DockStart 分发的 AutoDock Vina `--write_maps`，不新增外部依赖，也不改变 AutoGrid4 的 GPL 外部工具边界。
- 项目可在“运行时由受体计算网格”和“复用已保存 maps”之间切换；保存的 map set 不因切回实时网格而删除。
- manifest 绑定评分函数、受体 SHA256、请求 Box、实际偶数体素网格、spacing、Vina binary SHA256/大小，以及全部 map 文件与 payload SHA256。
- 生成或激活时用当前配体执行 `--maps ... --score_only` 兼容性探测；受体、Box、评分函数、Vina binary、map 内容或当前配体兼容性变化时阻断运行。
- run 冻结全部 maps、manifest、受体溯源快照、配体和配置；执行前后都核对路径与 SHA256。
- 实际 docking 命令只传 `--maps` 和对应的 `--scoring vina|vinardo`，不传 `--receptor`。报告必须明确这是 `grid-only / no-refine` 等价语义，不把它伪装成普通受体精修运行。
- GUI 已提供实时/已保存来源切换、生成、DockStart manifest 导入、raw maps 来源确认、状态与 manifest 详情。

2026-07-28 的源码验收已分别用随附 Vina 1.2.7 完成 Vina 与 Vinardo 的真实“生成 maps → 准备 run → `--maps` 执行 → 结果解析 → Markdown 报告”闭环。当前边界仍是刚性受体、单配体、全局对接；柔性侧链、评价模式、autobox、批量 maps 与非标准/大环 ghost atom 类型需要分别设计和回归。

科学证据收口并完成实际体验与 UI 优化后，进入发布阶段的剩余门禁包括：Basic/Assisted 安装态回归、历史项目兼容、异常中断恢复、长中文路径、亮/暗色 GUI 截图，以及安装、升级、重启和卸载生命周期。源码测试和开发态真实 Vina 运行不能替代这些发布门禁。

## 四级：AD4Zn 专用协议

状态：**AD4Zn beta 源码闭环已接入，尚未分配发布版本或重新打包**。

- AD4Zn 不是新的 Vina 评分函数。协议先对受体生成 TZ 几何伪原子，再以用户提供的 `AD4Zn.dat` 和专用 GPF 调用 AutoGrid4 生成 maps，最终由 Vina 使用 `--maps` 与 `--scoring ad4` 运行，不向 docking 命令传入 `--receptor`。
- Zn 环境识别以 Vina v1.2.7 `zinc_pseudo.py` 为固定算法参考：使用相同的距离键图、羧酸加权代表点、1-2/1-3 排除和三配位平面法向；只有恰好三个受体配位方向并保留稳定开放方向时才生成一个距 Zn 约 2.0 Å 的 TZ。近共面方向会被额外安全门禁阻止，不照搬上游脚本的整数截断数值怪癖。没有生成 TZ 时硬阻断，不回退到标准 AD4。
- 协议只用于单核 Zn 位点，不把多核位点或 Mg、Fe、Ca 等其他金属自动解释为 AD4Zn。目标 Zn 的 4.5 Å 邻域存在其他金属时直接阻止自动生成。
- 用户必须确认目标 Zn、配位原子/残基、质子化、水、辅因子、TZ 几何和 Zn-only 范围。确认记录绑定原始受体 SHA256 与选定站点；输入改变后自动失效。
- AutoGrid4 版本硬门禁为 4.2.7 或更高。专用 GPF 必须包含 `AD4Zn.dat`、TZ 受体及六条 AD4Zn `nbp_r_eps` 覆盖；只有 GLG 显示成功完成、必需 maps 齐全且文件/manifest 哈希一致时才允许运行。
- 项目和 run 冻结原始受体、TZ 受体、`AD4Zn.dat`、GPF、maps、协议记录、AutoGrid/Vina 版本与二进制证据、用户确认和 SHA256；AD4Zn scores/report 使用独立文件名，不覆盖标准 AD4 或 Vina/Vinardo 结果。

许可证边界：`AD4Zn.dat` 文件自身声明 GPL-2.0-or-later。DockStart 不内置、提交、自动下载或随 Basic/Assisted 分发该文件；用户从固定的 AutoDock Vina v1.2.7 上游参考取得并选择后，项目会保存用户触发的副本，并记录本机来源路径、实际 SHA256、许可证 ID、受支持参数配置和固定上游参考。分享含副本的项目时由分享者履行 GPL 再分发义务；任何应用内下载或随包方案都必须重新审查。

科学与发布边界：TZ 是网格势使用的几何伪原子，不代表真实原子或已验证的配位化学；AD4Zn 分值不能与标准 AD4、Vina 或 Vinardo 直接比较，也不能证明结合或药效。2026-07-30 已完成 1S63、2OI0、1R1J 的真实 TZ → AutoGrid4 → maps → Vina → 结果/报告验收，并覆盖无 TZ、多 Zn、非 Zn、参数篡改、AutoGrid/Vina 失败和项目恢复。该样本量仍不足以支持所有 Zn 环境或 Stable 声明；更广泛几何样本属于后续成熟度工作，Basic/Assisted 安装、历史项目和 GUI 则属于科学闭环后的体验与发布阶段。

## 五级：实验性协议

### 5.1 多配体共同对接（源码实验性闭环，待发布门禁）

状态：**当前工作树的实验性闭环已接入；本轮不改版本号、不重新打包，项目也尚未公开发布 Release。**

- 协议名称固定为“多配体共同对接（实验性）”，与多个配体分别运行的串行批量筛选分开保存、运行和解释。导入多个配体不会自动切换到本协议。
- 最低工具门槛为稳定版 AutoDock Vina 1.2.0，并要求当前可执行文件的帮助声明支持 `--ligand` 多路径输入。Vina 命令只写一个 `--ligand`，其后按冻结顺序传入两个成员路径。
- 首版只接受恰好两个已经准备好的 PDBQT、刚性受体、显式 Box、全局对接和 Vina/Vinardo 评分。成员输入、顺序、受体、配置、Vina 工具证据和输出进入独立运行记录。
- 每个输出 `MODEL` 表示两个成员组成的一组联合构象，只有一个联合评分。解析器和报告不得把联合评分拆成成员 affinity，不得把两个单配体分值相加，也不得直接比较成员组成或成员数不同的联合任务。
- stdout 结果表可能包含因 `energy_range` 最终未写入输出文件的行。Viewer 可用性与成员读取必须以 `out.pdbqt` 中实际存在、带结果记录的 `MODEL` 为准，不能用 stdout 行数生成不存在的构象。
- 明确排除柔性受体、AutoDock4、AD4Zn、Vina/Vinardo 预计算 maps、`score_only`、`local_only` 和水合对接；任何组合扩展都需单独设计与科学回归。
- 该能力只复用既有 AutoDock Vina 适配器与 PDBQT/项目基础设施，不新增第三方运行时、外部工具或许可证义务。

2026-07-28 已使用 AutoDock Vina 官方 5X72 双配体输入和仓库随附的 AutoDock Vina 1.2.7 完成源码级真实全链路，覆盖“一个 `--ligand` + 两路径”的真实命令、成员顺序、联合 `MODEL`/评分解析、构象查看、报告与哈希。官方参考输出 SHA256 为 `9fd1901bb52d0c767674fdd6e926f749e9995a35aa5b51e0318fd9e6f6922764`，解析得到 7 个双成员 Mode，最佳联合评分 -19.043 kcal/mol。2026-07-30 又以 4DM3 完成多 seed、受体电荷、Box、总自由度/可旋转键、结果审计和失败恢复验收；4DM3 是补充的独立工程/稳定性基准，不替代 5X72 的官方命令语义基准。两者都不能把联合分数解释成成员 affinity，也不能替代后续安装态和 GUI 体验验收。

### 5.2 水合 AutoDock4 对接（Experimental 源码闭环，待发布门禁）

状态：**`hydrated_ad4_experimental` 已在当前工作树中完成后端、桌面桥接和 GUI 实验性闭环；本轮不改版本号、不重新打包，项目也尚未公开发布 Release。**

它是独立的 AD4 maps 协议，不是“标准 AD4 加一个开关”，也不是多配体共同对接、串行批量筛选或普通显式水保留功能。打开项目后可从左侧“工作台 → 水合 AD4”进入，页面始终标记 Experimental；工作区按“准备水合配体 → 生成水合 maps → 运行前检查 → 创建并执行 run → 读取水合结果”逐步开放，标准单配体页面和标准配体文件不被覆盖。

**输入、工具与组合边界**

- 首版只接受一个项目内 raw SDF/MOL 配体；文件必须恰好包含一个可由 RDKit 读取的有效分子、一个三维 conformer、有限坐标和明确键级。准备过程使用兼容 RDKit + Meeko `MoleculePreparation(hydrate=True)`，能力探测必须实际生成 W 原子。
- 水合配体和加氢 SDF 保存到独立的 `protocols/hydrated/ligand_preparations/hydrated_ligand_NNN/` 审计目录；原始配体、标准 `prepared/ligand.pdbqt` 和当前标准协议均不被改写。
- 只支持项目内刚性受体 PDBQT、单配体、显式项目 Box、全局对接、AD4 maps 和 AutoDock Vina 1.2.0 或更高版本。当前 Vina 还必须明确通过 `--maps` 能力门禁。
- 明确排除柔性受体、串行批量筛选、多配体共同对接、Vina/Vinardo 评分或 maps、标准 AD4/AD4Zn 混用、Meeko 大环 ghost atom 组合、`score_only`、`local_only`、autobox 和跨配体评分比较。任何组合扩展都需要独立的数据模型、科学回归和发布门禁。
- AutoGrid4 继续是用户自行安装或配置的 GPL 外部工具，不进入 Basic/Assisted 安装包；本协议没有新增随包 GPL 组件。水合配体准备依赖兼容 RDKit/Meeko，正式发布前仍需明确 Basic、Assisted 与用户配置 Python 的实际可用边界。

**maps、运行与异常恢复**

- AutoGrid4 基础 GPF 不把 W 作为普通 ligand type 交给 AutoGrid；它为真实配体原子类型补齐 OA/HD 源 map，再由 DockStart 的 clean-room 实现生成 `receptor.W.map`。
- W map 固定使用第一版 BEST 规则：OA/HD 权重均为 `1.0`；任一源值大于 `0` 时写入置换熵 `-0.2 kcal/mol`；否则取更有利的源值并乘以 `0.6`。OA、HD、W 的几何、来源、参数、文件大小和 SHA256 必须完全绑定。
- 生成成功后保存独立 `maps/hydrated_NNN/hydrated_manifest.json`；受体、水合配体、配体 manifest、Box、maps 或 manifest 改变时，旧记录失效并阻止运行。AutoGrid4 只在生成阶段需要；已发布且完整性有效的 maps 不因之后移除生成工具而自动失效。
- `prepare_hydrated_run` 把刚性受体、水合配体、两个 manifest、全部 maps、配置和 Vina 二进制证据复制进 `runs/run_NNN/` 不可变快照。Vina 命令使用 `--maps <prefix> --scoring ad4`，不传 `--receptor`，因此属于预计算网格的 `grid-only / no-refine` 等价语义。
- Vina 成功后先保留原始 `out.pdbqt`，再进入显式 `postprocessing` 阶段。后处理失败时 run 以 `postprocess_failed` 终止并保留 raw 输出，不把不完整派生物标记为成功；该 CPU 内后处理阶段不接受取消，进程异常中断时保守收敛为失败，用户需要重新运行。

**结果与评分语义**

- 每个 MODEL 独立处理。W 身份只按 PDBQT 最终原子类型列判断，不按原子名猜测；后处理不改写 `REMARK VINA RESULT`、构象顺序、扭转树或非 W 原子。
- W 与配体重原子或受体非 HD 原子距离严格小于 `2.03 Å` 时移除；其余 W 在 `±1 Å` 邻域采样 W map 最小值，`< -0.5` 记为强水，`< -0.3` 记为弱水，其余记为置换水。
- run 同时保留原始 `out.pdbqt`、带强/弱水注释的 `hydrated_retained.pdbqt`、去除全部 W 的 `ligand_water_free.pdbqt` 和 `waters_manifest.json`。Viewer 默认使用保留水构象；需要 SDF 导出时只允许使用与本次 run 绑定并通过哈希校验的去水派生文件。
- `results/hydrated_ad4_scores.csv`、run/project Markdown 报告和结果页显示的仍是原始 hydrated AD4 affinity。它只用于同一 run 内 pose 排序，不用于虚拟筛选、跨配体、跨协议或处理前后分值比较。
- 水分子分类只是结构派生与记录，**处理后评分未计算**。界面和报告不得出现“校正 affinity”“dry affinity”或暗示保留/置换水已完成重评分的字段。

**源码验证证据**

- 自动化测试覆盖水合准备的输入/工具/TOCTOU/篡改门禁，BEST W map 数值和几何，AutoGrid hydrated profile，项目级 maps 生成与失效，逐 MODEL 后处理阈值与扭转树保护，run 冻结、执行、失败、取消/恢复、结果/报告，以及桌面 Tauri API 参数映射。结果加载还严格复核 Vina verbosity 1/2 的评分表词法格式、Mode 连续性、affinity 排序、RMSD 边界、`num_modes`、`energy_range` 造成的 PDBQT 连续前缀、stdout/log 字节一致性以及已登记 CSV 与冻结日志的一致性；未登记 CSV 不能覆盖冻结日志。
- 外部验收 fixture 只提交元数据和固定 SHA256，不复制上游结构或结果文件。验收脚本要求用户提供 AutoDock Vina v1.2.7 固定 tag/commit 的 1UW6 checkout，并使用随附 Vina 1.2.7 与 Meeko 0.7.1 复核四个独立环节：水合配体准备生成 2 个 W；clean-room BEST W map 的 68,921 个网格值与上游参考逐点一致（DockStart 规范化序列化后的文件字节与上游文件不同，不宣称 SHA256 相同）；Vina AD4 maps 运行最佳评分为 `-8.261 kcal/mol`，请求最多 9 个 Mode 时实际输出 8 个；官方独立 9-pose raw 输出的 18 个 W 被分类为 8 个强水、1 个弱水和 9 个置换水且 raw affinity 不变。
- 2026-07-30 在当前工作树上以公开 2BYS/2ZJU、Python 3.11.15、RDKit 2026.03.3、Meeko 0.7.1、AutoGrid4 4.2.7 和 Vina 1.2.7 完成项目 API 真机链。两套系统均通过标准导入/准备、真实 GPF/maps/W map、冻结 run、Vina、逐 Mode 水守恒、scores/report 字节发布一致性及临时环境清理；2BYS 输出 1 个 Mode，2ZJU 输出 6 个连续 Mode。其参考 RMSD 分别为 `12.760 Å` 和 `6.422 Å`，验收器明确记录 `scientific_success_claimed = false`。
- clean-room 结果有意不复刻上游 `dry.py` 的 7 强/4 弱/7 置换统计：上游脚本按原子名 `W` 识别水并将 map 原点偏移半个 spacing；DockStart 按最终 PDBQT 类型 `W` 和真实 AutoGrid 原点处理。旧脚本统计只作为只读兼容证据，不是运行时规则。

**科学成熟度仍需完成**

1. 增加至少一个独立共晶体系，以预先冻结的参考构象、原子映射和 RMSD 阈值取得正向姿势恢复；2BYS/2ZJU 的工程通过不得冒充该证据。
2. 扩大不同配体、口袋含水环境、Box/spacing 和 Vina/Meeko/AutoGrid 版本组合，并继续保留失败案例。1UW6 加两套负面姿势诊断不足以支持 Stable 声明。
3. 大网格、并发生成、终止/恢复、maps 替换、迁移和模拟写入失败已经覆盖；仍可在后续硬件基准中补充物理磁盘不足与资源上限测量，但不再把这些写成协议主链未实现。

Basic/Assisted 干净安装、升级、重启、卸载、长中文/空格路径、亮暗主题截图、用户指南和发布材料属于科学闭环后的体验与发布阶段，本阶段不执行。完成正向科学基准前必须保持 Experimental，不进入默认入门流程，也不能与 Stable 协议结果混排。

这些协议单独标记为实验性，不进入默认入门流程，也不与标准刚性对接混用参数页面。
