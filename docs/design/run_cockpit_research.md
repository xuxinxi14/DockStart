# DockStart 运行驾驶舱：产品研究与实施边界

日期：2026-07-12  
基线：DockStart v0.9.4，本轮整改开始前的仓库能力  
用途：为“运行对接”驾驶舱的功能整合、科学措辞和后续迭代排序提供依据。本文不构成第三方软件复刻方案。

## 1. 结论先行

DockStart 当前已经跑通单受体、单配体的 AutoDock Vina 最小闭环，也已经具备项目化文件管理、3D 查看、Box 保存、运行记录、结果解析和 Markdown 报告。最明显的问题不是“完全没有功能”，而是能力分散在多个页面，运行前状态、结构预览、参数、日志和下一步之间缺少一个可检查、可修复、可执行的统一工作面。

本轮应对齐成熟软件的**工作流完成度**，而不是声称达到其算法、验证体系或商业产品覆盖范围：

- 可以借鉴引导式配置、运行前检查、任务状态、日志可见性、表格与 3D 联动等交互模式。
- 不复制 Schrödinger 的界面素材、产品文案、专有算法或代码，不仿造其品牌识别和 trade dress。
- 不把 AutoDock Vina 包装成 Glide，也不声称 DockStart 的 docking 准确率、富集能力或适用范围等同商业算法。
- 不因效果图出现某个字段就伪造能力；所有“就绪”“版本”“磁盘空间”“运行状态”必须来自真实检测。

## 2. 一手资料与成熟项目观察

| 资料 | 可借鉴的产品模式 | 不应直接照搬的部分 |
| --- | --- | --- |
| [AutoDock Vina：Basic docking](https://autodock-vina.readthedocs.io/en/stable/docking_basic.html) | 从受体/配体准备、Box、参数到输出构象的完整路径；配置与实际命令保持可追踪 | 教程中的特定分子、坐标、参数和期望分数不是通用默认值 |
| [AutoDock Vina：FAQ](https://autodock-vina.readthedocs.io/en/stable/faq.html) | 对 Box 体积、`exhaustiveness`、配体柔性和搜索行为给出真实解释；大 Box 应警告 | 不应把耗时写成固定的“2–5 分钟”，也不应把 Vina 写成遗传算法 |
| [AutoDock Vina：Documentation](https://autodock-vina.readthedocs.io/en/latest/) | 参数、命令行、Python API、单配体和 batch 能力均有官方定义 | 当前 DockStart 不能仅因 Vina 支持 batch 就宣称自己已经支持批量工作流 |
| [Schrödinger Glide](https://www.schrodinger.com/platform/products/glide/) | 供应商页面强调引导式 GUI、模型设置、约束与虚拟筛选；适合作为“减少设置错误”的产品基准 | 页面包含商业宣传表述，不可当作独立性能证据；Glide 的算法和能力不属于 DockStart |
| [Schrödinger Jobcontrol](https://learn.schrodinger.com/public/python_api/2025-4/jobcontrol.html) 与 [Job Monitor table](https://learn.schrodinger.com/public/python_api/2026-2/api/schrodinger.application.job_monitor.job_monitor_table.html) | 异步任务、状态/进度、最近更新时间、停止/取消、日志与结果归集，是成熟计算软件的重要工作面 | DockStart 当前为本地同步调用，不能先做一个假的进度条或取消按钮 |
| [UCSF ChimeraX Dock Prep](https://www.cgl.ucsf.edu/chimerax/docs/user/tools/dockprep.html) | 将水、离子、altloc、缺失侧链、氢和电荷等准备事项显式列出，并提醒用户保留可能重要的组分 | 这些步骤是带科学判断的结构准备，不可简化成“一键科学正确” |
| [UCSF ChimeraX ViewDockX](https://www.cgl.ucsf.edu/chimerax/docs/user/commands/viewdockx.html) | 构象列表与 3D 场景联动、上下一个构象和键盘操作，适合结果复核 | DockStart 目前只应在已有 Vina 输出和评分文件上实现联动，不扩展为未经验证的相互作用结论 |
| [PyRx 官方站点](https://pyrx.sourceforge.io/) | docking wizard、易用界面和结构/虚拟筛选工作流说明，证明新手需要向导而非裸参数表 | 未完成许可证和分发条款审查前，不复制其源码、图标、文案或打包资源 |
| [AutoDock-Vina GitHub](https://github.com/ccsb-scripps/AutoDock-Vina) | Apache-2.0；官方实现、版本和发布记录是工具检测的权威来源 | 显示版本必须取自本机实际检测，不能把截图中的版本写死 |
| [GNINA GitHub](https://github.com/gnina/gnina) | CNN scoring/optimization、GPU 和结果重打分体现了“可插拔 docking engine”的长期价值 | GNINA 含 Apache/GPL 双许可证路径，Open Babel 触发 GPL；GPU/模型依赖也明显超出本轮范围 |
| [Webina GitHub](https://github.com/durrantlab/webina) | 在本机执行、文件留在用户设备、设置与结果分析同一工作流，是隐私与低门槛的好范式 | 仓库已归档，且 WebAssembly 浏览器架构不适合直接替换当前 Tauri + 本机 Vina adapter |
| [EasyDock GitHub](https://github.com/ci-lab-cz/easydock) | 自动准备、结果数据库、可恢复运行、分布式执行和多引擎 adapter 说明了批量系统的成熟形态 | 本轮是单次对接驾驶舱，不引入 SQLite、Dask、容器、批量筛选或其第三方依赖链 |
| [AMDock GitHub](https://github.com/Valdes-Tresanco-MS/AMDock-win) | 受监督的准备、Box 定义、运行和结果分析可以组织为少量清晰阶段 | AMDock 为 GPL-3.0，不能将其代码或资源直接复制到 DockStart 的 Apache 核心中 |

这些资料共同指向五个高价值原则：

1. 在点击运行前，把输入、Box、参数、工具、输出位置和可复现信息集中展示。
2. “检查通过”只代表**计算运行条件**满足，不代表受体/配体的质子化、电荷、结构修复或生物学选择正确。
3. 长任务必须让用户看见状态、日志和产物；取消、恢复和队列必须建立在真实后台任务模型上。
4. 结果表应能驱动 3D 构象查看，但 docking score 仍只是特定输入、参数和评分函数下的计算结果。
5. 单次工作流稳定之后，再考虑 batch、多引擎、约束和相互作用分析。

## 3. 当前能力与缺口矩阵

以下“当前能力”以本轮开始时的源码为准。

| 能力域 | 当前已有 | 对标目标 | 主要缺口 | 优先级 |
| --- | --- | --- | --- | --- |
| 项目与数据 | 独立项目目录、`project.json`、raw/prepared/configs/runs/results/reports 分层 | 项目上下文始终可见，保存状态明确 | 运行页缺少项目时间、目录、最近运行等集中摘要 | P0 |
| 结构获取/准备 | RCSB/PubChem raw 获取；RDKit/Meeko 能力检测和最小受体/配体准备；也可直接导入 PDBQT | Dock Prep 式的显式准备清单 | 仍不能验证质子化、电荷、金属、辅因子、缺失残基是否科学合理 | P1，先提示不自动裁决 |
| 3D 查看 | 3Dmol.js 查看 raw/prepared/pose，Box 可视化与保存 | 运行页内同时查看受体、配体和 Box | Viewer 与运行前检查分离，用户需跨页核对 | P0 |
| Box | 中心/尺寸表单、有效性校验、大尺寸 warning、与 Viewer 同字段保存 | 预览旁直接查看/编辑并立即重检 | 缺少体积摘要、与运行检查的一屏反馈 | P0 |
| Vina 参数 | `exhaustiveness`、`num_modes`、`energy_range`、`cpu`、`seed` 的保存和中文说明 | 运行设置一屏复核，默认值与风险可见 | 参数页、配置页和运行页割裂；效果图中的“GA”字段科学错误 | P0 |
| 配置生成 | 预览并写入 `configs/vina_config.txt` | 主操作可串联生成配置 | 用户需单独进入配置页；缺少一键链路中的明确阶段状态 | P0 |
| 运行前检查 | 已检查 project、PDBQT、config、Box、Vina 参数和 Vina 检测 | 右侧常驻 checklist，阻塞项可定位修复 | 缺少输出目录可写、磁盘空间、文件基础摘要，以及同屏修复入口 | P0 |
| 分子摘要 | Viewer 可读结构文本 | 显示链、原子数、配体可旋转键信息，帮助发现选错文件 | 当前没有统一、可复用的 PDBQT 摘要；摘要也不能冒充化学质量验证 | P0 |
| 运行审计 | `run_NNN`、metadata、命令预览、配置快照、stdout/stderr/log/out 和 exit code | 每次运行可复现、可追踪 | 能力存在但被分散在准备页、执行页和高级详情中 | P0 |
| 任务执行 | 调用本地 Vina，完成后保存产物和状态 | Job Monitor 式进度、日志流、停止/取消 | 当前同步调用，没有真实流式进度和取消语义 | P1 |
| 结果 | 解析 Vina log、导出 `scores.csv`、查看 pose | ViewDockX 式表格与 3D 联动 | 结果表、pose 和运行驾驶舱仍是分开的上下文 | P1 |
| 报告 | 导出 Markdown 报告并保留免责声明 | 从成功运行直接到可分享报告 | 缺少驾驶舱内的自动解析/明确交接状态 | P0/P1 |
| 诊断 | 工具链页、安装后自检、Basic/Assisted/Demo Mode 分级 | 运行前只显示本次任务相关的关键技术信息 | 全局诊断与当前项目运行条件未完全合并 | P0 |
| 批量与恢复 | 当前以单次 run 为中心 | Vina batch、EasyDock 的数据库/恢复/队列 | 没有批量任务、断点续跑或结果数据库 | P2 |
| 多引擎/高级协议 | Vina adapter；不修改 Vina 算法 | 可选引擎、柔性/约束等高级协议 | GNINA、AD4/Vinardo、约束等均需独立科学与许可设计 | P2 |

## 4. P0 / P1 / P2 实施边界

### P0：本轮必须做成的运行驾驶舱

P0 只整合或补齐当前单次 Vina 工作流，不引入新的 docking engine、科学算法或外部依赖。

- 新增统一“运行对接”驾驶舱：结构预览、Box、受体/配体摘要、Vina 参数、输出信息、工具来源和前置检查同屏呈现。
- 复用现有 Viewer、Box 保存、参数保存、config 生成、run prepare、Vina execute、结果解析接口；不另造第二套数据模型。
- 前置检查补充真实的输出目录可写性、可用磁盘空间、项目内路径边界和 PDBQT 基础摘要。
- 主按钮按确定顺序执行“保存参数/Box → 生成配置 → 创建 run → 执行 Vina → 解析结果”，任一步失败立即停止并保留已生成的审计文件，不静默覆盖已有 run。
- 显示 adapter 实际检测到的 Vina 路径、来源和版本；不写死“内置 1.2.5/1.2.7”。
- PDBQT 摘要只陈述可直接解析的事实，例如 ATOM/HETATM 记录数、链标识、`BRANCH`/`TORSDOF` 信息；必须标注“基础文件摘要，不代表结构准备正确”。
- 运行成功后给出明确的结果和 pose 入口，并保留 DockStart 科学免责声明。
- 保持既有 `project.json` 与 `runs/run_NNN` 兼容，不迁移或破坏用户项目。

### P1：任务管理和结果复核

P1 需要先把同步执行重构为真实后台任务，再提供界面能力。

- 流式 stdout/stderr、真实阶段进度、停止/取消与失败恢复；不能用定时器伪造百分比。
- 根据本机**历史同类运行**给出带置信边界的耗时范围；没有历史数据时只显示影响因素和“暂无法可靠估计”。
- 结果表与 3D pose 双向联动，支持上一/下一构象和键盘操作。
- 增加运行历史筛选、重新打开日志、复制命令、定位产物等 Job Monitor 式操作。
- 增加结构准备复核清单，但将水、金属、辅因子、链选择、质子化和电荷保留为用户判断，不自动宣称正确。

### P2：批量、恢复与高级协议

P2 在单次流程稳定后另立设计和许可证审查，不在当前驾驶舱中以占位按钮制造“已支持”的错觉。

- Vina batch、多配体队列、断点恢复、SQLite 结果索引和跨运行比较。
- 可选 docking engine adapter，例如 GNINA；必须单独处理 GPU、模型文件、Open Babel/GPL 路径和打包体积。
- AD4/Vinardo、柔性侧链、共价/水化 docking、约束和自动口袋建议；每项都需要独立输入校验、结果解释和回归样例。
- PoseBusters、ProLIF/PLIP 等结果复核或相互作用分析只能作为明确标注的附加计算，不能把结果提升为实验事实。

## 5. 许可证与代码复用边界

| 项目/产品 | 已知边界 | DockStart 决策 |
| --- | --- | --- |
| Schrödinger Glide / Maestro | 商业专有产品与文档 | 只研究公开工作流模式；不复制代码、图标、截图素材、专有文案或算法，不做像素级仿制 |
| AutoDock Vina | Apache-2.0 | 可通过 adapter 调用，候选内置时保留许可证、版本和来源；仍不得暗示是 DockStart 自研算法 |
| GNINA | Apache/GPL 双许可；使用 Open Babel 的路径受 GPL 约束 | 本轮不集成；未来只作为隔离的可选外部 adapter 评估，并重新审查分发方式 |
| Webina | Apache-2.0；仓库已归档 | 只借鉴本地执行和隐私模式，不复制其应用实现或替换现有运行架构 |
| EasyDock | BSD-3-Clause；集成工具各有独立许可证 | 只借鉴可恢复任务、数据库和多引擎抽象；不复制代码或依赖链到 P0 |
| AMDock | GPL-3.0 | 只做功能层观察；禁止把其代码、资源或 bundled runtime 复制进 DockStart Apache 核心 |
| PyRx | 官方站点未提供足以支撑本轮分发决策的完整许可结论 | 只借鉴 wizard 思路；未经专项审查不复制或内置 |
| ChimeraX | 软件和素材有独立使用条款 | 只引用公开文档中的工作流事实；不复制界面资产或代码 |

任何新增依赖或二进制进入发布包前，都必须更新 `docs/license_notes.md`，记录用途、版本、许可证、集成方式、是否内置以及源码/许可证获取方式。

## 6. 科学措辞修正

| 不应使用 | 建议使用 | 原因 |
| --- | --- | --- |
| “搜索算法：遗传算法（GA）” | “搜索策略：AutoDock Vina 随机全局搜索，并在步骤中使用 BFGS 局部优化”或更简洁的“由 AutoDock Vina 实现（非 GA）” | Vina FAQ 描述的是随机起点、随机扰动、局部优化与选择；GA 是 AutoDock4 常见表述，不适用于 Vina |
| “预计耗时：2–5 分钟” | 首次显示“暂无法可靠估计”；列出配体柔性、Box、`exhaustiveness`、CPU/线程、硬件和 Vina 版本等影响因素 | Vina 的每次 run 步数由配体大小/柔性等启发式决定，`exhaustiveness` 还影响独立 run 数和并行度，固定分钟数不可信 |
| “所有前置条件已满足” | “本次计算运行条件已满足” | 文件存在和工具可执行不等于结构准备科学正确 |
| “受体/配体已就绪” | “PDBQT 文件已找到并通过基础检查；质子化、电荷、金属/辅因子等仍需用户确认” | 避免把文件级检查扩大为科学验证 |
| “Box 是结合位点” | “Box 是 Vina 搜索空间；位置与大小需要基于结构或实验信息判断” | Box 只限制搜索范围，不能证明真实结合位点 |
| “结合能证明该分子有效” | “该构象在当前输入、Box、参数和评分函数下得到相应 docking score，仅供结构结合趋势参考” | docking score 不能直接证明真实结合、药效、安全性或临床价值 |
| “堪比 Schrödinger/Glide 的对接能力” | “以成熟商业软件为工作流与交互参考，核心计算仍为 AutoDock Vina” | 产品完成度、算法能力和验证证据必须分开表述 |

报告和结果页继续保留：

> Docking score 仅供结构结合趋势参考，不能替代实验验证。

## 7. P0 验收标准

P0 完成不以“效果图看起来接近”为唯一标准，而应同时满足：

1. 使用真实 Demo 项目完成导入/确认 PDBQT、Box 与参数保存、配置生成、run 创建、Vina 执行、结果解析和报告导出。
2. 驾驶舱中的受体、配体、Box、Vina、输出目录、磁盘空间和工具版本均来自实际项目或系统检测。
3. 任一输入缺失、输出不可写或 Vina 不可用时，主操作被阻止，并给出能定位到现有页面的中文修复建议。
4. 运行目录保留 config snapshot、命令、stdout、stderr、`log.txt`、`metadata.json`、`out.pdbqt`、exit code 和时间信息。
5. 3D 预览、参数摘要和 checklist 不因窄屏或长路径遮挡主操作；键盘 focus、禁用态和错误态可辨认。
6. 不出现 GA、固定分钟耗时、虚假“科学就绪”、写死工具版本或“等同 Glide”之类不受证据支持的表述。

