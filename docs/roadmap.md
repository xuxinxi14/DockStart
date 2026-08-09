# DockStart Roadmap

本文档记录 DockStart 从 V0.1 Lite MVP 走向 DockStart Full 一站式分子对接平台的阶段路线。实际优先级会根据用户反馈、许可证边界、分发体积和维护成本调整。

## 当前状态覆盖（2026-08-05）

- 当前源码为 v0.14.3 非最终本地候选，本轮重新生成 Basic 与 Assisted 本地候选安装包，但不创建公开 Release；
- v0.14.0 已形成标准 AD4 有限柔性、刚性串行批量和刚性双配体共同搜索的源码闭环；
- v0.14.1 为上述三条路径增加独立外部验收器，并已用固定官方输入、Vina 1.2.7 与调用者固定 SHA256 的 AutoGrid 4.2.6 实跑通过；
- 下文旧版本号、未来版本设想和“尚未修改版本号”等文字保留为历史路线记录，不覆盖本节与当前源码事实。

## V0.12.0：AutoDock4 Maps 基础设施，已完成

完成路径：

1. **外部工具边界**：完成 AutoGrid4 GPL 审查；Basic/Assisted 均不内置，只检测用户配置路径或 PATH。
2. **网格准备**：从当前受体、配体和 Box 推导 GPF，管理偶数 grid points、spacing、原子类型和可选参数文件。
3. **maps 资产**：生成或导入 maps，保存 manifest、日志、版本、命令和 SHA256，并把 maps 绑定到受体与 Box。
4. **运行协议**：新增独立 `ad4_maps`，通过 Vina 1.2.7 的 `--maps`、`--scoring ad4` 执行；不复用普通评分下拉框。
5. **结果隔离**：AD4 项目汇总写入 `ad4_scores.csv` 与 `ad4_docking_report.md`，运行历史、结果页和报告显示协议标签。
6. **科学回归**：Scripps 官方 `1dwd` 用例完成真实 AutoGrid4 4.2.6 + Vina 1.2.7 全链路回归。
7. **发布**：统一版本为 v0.12.0，构建 Basic/Assisted 的 MSI 与 NSIS；AutoGrid4 不进入任何安装包。

## 当前源码增量：Vina / Vinardo 预计算 maps 复用

该增量尚未分配发布版本，也没有重新打包。它与 v0.12.0 的 AutoDock4 maps 是两条不同路径：

1. 使用现有 Vina 1.2.7 的 `--write_maps` 生成 Vina 或 Vinardo maps，不依赖 AutoGrid4。
2. 保存严格 manifest，绑定评分函数、受体、请求 Box、实际网格、Vina binary 和全部 map payload。
3. 生成、导入和重新启用时，对当前配体执行 maps 兼容性探测。
4. run 冻结 maps 与 manifest，使用 `--maps` 执行，并在 Vina 返回后再次核对所有不可变输入。
5. GUI 明确显示 `grid-only / no-refine`、刚性受体、单配体和全局对接边界。
6. 随附 Vina 1.2.7 已分别完成 Vina 与 Vinardo 的真实生成、复用、解析和报告回归。

## 当前源码增量：AD4Zn beta

该协议已进入源码工作树，但尚未修改版本号、重新打包或进入正式 Release：

1. AD4Zn 不是新的 Vina 评分函数。DockStart 先生成 TZ 受体，以用户提供的 `AD4Zn.dat` 和 AutoGrid4 4.2.7+ 生成专用 maps，最后使用 Vina `--maps ... --scoring ad4`。
2. 仅处理单核 Zn 位点。只有恰好三个受体配位方向并存在稳定的开放四面体方向时才生成 TZ；近共面或 4.5 Å 邻域含其他金属时阻止自动生成。没有 TZ 时阻止运行，不回退为标准 AD4，也不把多核位点或 Mg、Fe、Ca 等其他金属泛化为 AD4Zn。
3. 用户显式确认目标 Zn、配位环境、质子化、水、辅因子、TZ 几何和 Zn-only 范围；确认绑定受体 SHA256 和站点。
4. `AD4Zn.dat` 文件自身声明 GPL-2.0-or-later，DockStart 不随仓库/安装包内置，也不自动下载。用户选择后会复制到项目，记录本机来源路径、实际 SHA256、许可证 ID、受支持参数配置与固定上游参考；分享项目副本时需履行 GPL 再分发义务。
5. 专用 GPF、GLG 成功状态、maps、manifest、原始/TZ 受体、参数、AutoGrid/Vina 工具证据和 run 前后 SHA256 共同组成硬门禁。
6. scores/report 与标准 AD4、Vina/Vinardo 隔离；界面和报告明确 TZ 是几何伪原子，协议分值不能跨评分体系直接比较。

发布仍需完成 1S63、多样本 Zn 环境、无 TZ/多 Zn/非 Zn/参数或 maps 篡改/AutoGrid 失败路径、历史项目、Basic/Assisted 干净安装和 GUI 回归。源码测试或单个开发态基准不能替代这些门禁。

## 当前源码增量：多配体共同对接（实验性）

该协议已形成源码实验性闭环，但尚未修改 v0.12.2 版本号、重新打包或进入正式 Release：

1. 它与串行批量筛选分开：批量筛选让多个配体各自运行；共同对接让恰好两个已准备 PDBQT 在同一次 Vina 搜索中联合运行，且必须由用户显式选择。
2. 最低要求 AutoDock Vina 1.2.0；命令使用一个 `--ligand`，后面按冻结顺序传入两个成员路径。
3. 首版只支持刚性受体、显式 Box、全局对接和 Vina/Vinardo；不支持柔性受体、AD4/AD4Zn、预计算 maps、`score_only`、`local_only` 或水合对接。
4. 一个输出 `MODEL` 是一组联合构象，只有一个联合评分。结果和报告不生成成员 affinity，也不直接比较成员组成或数量不同的任务。
5. stdout 表格可能列出未最终写入 `out.pdbqt` 的 Mode；构象可用性以输出文件实际存在的 `MODEL` 为准。
6. 复用现有 AutoDock Vina 与 PDBQT 基础设施，不增加第三方依赖、外部工具或许可证义务。

2026-07-28 已用官方 5X72 双配体输入和仓库随附的 AutoDock Vina 1.2.7 完成源码级真实全链路，覆盖一个 `--ligand` + 两个有序路径、联合构象解析、双成员 Viewer、评分表和报告；官方参考输出 SHA256 为 `9fd1901bb52d0c767674fdd6e926f749e9995a35aa5b51e0318fd9e6f6922764`，成功解析 7 个双成员 Mode，最佳联合评分 -19.043 kcal/mol。正式发布仍需 Basic/Assisted 安装态、历史项目、失败路径和 GUI 门禁。

后续顺序：

```text
v0.12.1–v0.12.2 之后的当前源码：score_only/local_only 定量闭环、高级参数、批量完整结果与归档、Vina/Vinardo 预计算 maps 复用、AD4Zn beta、多配体共同对接实验性闭环
可选 v0.12.x 维护 Release：只收口已通过门禁的能力；构建时必须隐藏/剔除尚未过门禁的 AD4Zn 与多配体共同对接入口
v0.13.0 发布候选：若保留当前 AD4Zn 入口，下一正式 Release 就必须是通过独立科学、许可证、安装态和 GUI 门禁后的 v0.13.0
v0.14.0：AD4Zn stable，前提是 beta 完成多样本、失败路径和安装态门禁
更后：多配体共同对接单独通过发布门禁；水合对接等候选能力逐项立项，不提前绑定版本号
v1.0.0：冻结 Stable 协议集合并完成兼容、签名、文档和发布门禁
```

2026-07-27 的真实工具链证据已覆盖 50 配体批筛、失败隔离与取消恢复、1FPU 柔性侧链、BACE_1 大环和标准 AutoGrid4/AD4。后续发布收口不再把这些能力列为“尚未开发”，但仍必须复核当前源码重新打包后的 GUI 和安装生命周期。

V0.12.0 明确不包含 AD4Zn、多配体共同对接、柔性受体 AD4、批量 AD4 maps、水合对接、分子动力学或评分函数修改。AD4Zn beta 与多配体共同对接都是该 Release 之后的源码增量，不能反向写入 v0.12.0 安装包能力。

`score_only` 只评价已有输入姿势，不进行全局搜索，也不生成新构象。新准备的 `local_only` 在同一不可变输入、配置和评分协议下先运行独立 `score_only` 基线，再执行局部优化并输出 `optimized.pdbqt`；结果记录输入与优化后评分、能量项和“优化后－输入”差值。几何变化按严格原子身份在同一受体坐标系直接计算未对齐重原子 RMSD、平均/最大位移和质心位移，该指标不是共晶参考 RMSD。现代双阶段结果默认在同一 3D 场景叠合本次 run 冻结的受体、输入姿势与优化后姿势，叠合不做额外对齐，读取前复核关键文件 SHA256；页面也可只显示任一姿势，不产生全局 pose 排名。帮助页、新建项目页和项目总览已增加独立的评分/局部优化入口，并与文件来源选择分离。评价模式启动前，用户必须在同场 3D 视图确认配体已位于受体中的待评价位置；确认绑定本次运行实际使用的刚性受体、可选柔性侧链与配体 SHA256，替换任一输入或重建柔性受体后自动失效。单次运行已接入 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine`、`force_even_voxels` 与可空的 `unbound_energy`，按运行模式和评分协议只写入有效参数，并对 Box × spacing × 原子类型的网格内存做运行前保护。`unbound_energy` 仅在 Vina/Vinardo、刚性单配体 `score_only` 中生效；显式 `0` 与留空不同，柔性受体、全局对接、局部优化、AutoDock4 maps 和批量筛选均不写入。需要门禁的选项必须通过当前 Vina 的高级帮助和最低版本检查，准备与执行阶段均会复核；`autobox` 要求稳定版 Vina 1.2.3 或更高版本，`unbound_energy` 最低门槛按 SemVer 与稳定版 Vina 1.2.4 比较，同核心号预发布版不通过。对新生成的 `score_only` run，分析前还会核对日志 SHA256；显式参考必须具有编号正确且有限的第 (1)～(4) 项，第 (4) 项与运行快照一致，且总评分满足 `(1) + (2) + (3) - (4)`。生成后的 `evaluation.json` 同样记录 SHA256，界面读取和报告生成前均会复核。AD4 评价项目报告使用 `ad4_score_only_report.md` 或 `ad4_local_only_report.md`，不会覆盖 Vina/Vinardo 评价报告。旧项目缺少新字段时保持 Vina 默认行为；旧单阶段 `local_only` run 仍可读取，但不会补造输入基线。这些内容目前只代表源码接入，不修改版本号、现有安装包或发布状态。

同一源码工作树中的串行批量筛选现在继承适用于全局对接的 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine` 和 `force_even_voxels`。六项值会冻结到队列状态、进入逐配体 config、整批报告与双归档协议指纹；建队后界面持续显示本队列冻结的 Box 与 Vina 快照。两个专家开关受当前 Vina 运行时能力门禁，能力在开始或恢复队列时统一复核。每次 attempt 在启动前和 Vina 返回后都会核对本次独立目录中的受体、配体、配置及当前 Vina 二进制的大小与 SHA256，并在 `attempt.json` 保存实际输入、配置和工具证据；归档详情及双归档比较继续复核已保存的 attempt 输入与配置，Vina 二进制本身不随归档复制。批量队列仍不使用只适用于刚性单配体 `score_only` 的 `unbound_energy`。该增量同样不修改现有版本号、安装包或发布状态。

有效的批量历史归档现可从列表或详情另存为单个 ZIP。导出包只包含经 allowlist 核对的只读归档 payload，并在根清单中记录成员大小、SHA256、payload 树哈希、源完整性结论和兼容警告；写入后再次执行 ZIP CRC、成员集合和逐文件哈希验证。现代归档会把 `attempt.json` 与冻结 attempt 记录交叉核对，并记录完整汇总、Top N、Markdown 实验记录及构象输出的历史大小与 SHA256；旧归档缺少凭据时只降级为部分验证，不会补造历史证据。GUI 始终使用“不覆盖”发布；目标已存在时保留原文件并要求另选名称。导出不修改归档，不包含 Vina 二进制、活动队列、项目当前状态或 staging，也不能直接导入为项目或恢复运行。包内既有审计 JSON 可能含绝对本机路径，因此它不是匿名化交换格式；清单哈希用于验证导出包字节，不等同于历史真实性证明或数字签名。本增量不迁移 `project.json` / `screening.json` schema，不新增依赖。

批量配体导入已改为 record 级闭环：支持选择多个文件或递归选择目录，多分子 SDF 不再只处理第一条有效记录。导入前先检查源容器、逻辑记录数、单记录和总输入上限；SDF/MOL 在独立 RDKit/Meeko worker 中逐条准备，单条化学错误进入预览但不阻断其他记录。准备后按 PDBQT 字节 SHA256 去重，重复项和失败项不可加入队列；用户可在创建前全选、清空或逐条勾选可用记录。staging index 以加法字段保存源文件、1-based record index、record SHA256 和全部重复来源，建队时交叉核对并把来源冻结到任务和 attempt。当前尚未冻结原始 record 拓扑字节，因此批量 SDF 汇总仍明确不可用。本增量不修改版本号、安装包、外部依赖或既有 schema。

新队列同时冻结并哈希全部九项 `resource_limits`。创建、开始、恢复、归档详情与双归档比较共用同一严格校验，覆盖重试次数、Top N、配体数量、单配体与总输入字节预算，以及 CPU、搜索彻底程度、构象数和 Box 上限；资源限制本身不能放宽超过应用硬上限。旧记录缺字段时按默认上限推断并保持可读，但不会冒充现代完整证据。

当前 `project.json` 仍使用 schema v1，并通过向后兼容字段保存批筛、柔性、大环和 AD4 maps 状态。schema v2 不再作为一个脱离实际需求的固定版本目标；只有新协议无法由现有数据模型安全表达时，才在提供备份、回滚和旧项目兼容测试后单独迁移。文档和界面必须使用“串行批量筛选”表示多个配体分别运行，使用“多配体共同对接（实验性）”表示恰好两个成员进入同一次搜索，不能再用“多配体对接”含混指代两者。

## 产品方向

DockStart Full 的最终目标：

- 分发简单；
- 内置工具链；
- 开箱即用；
- 中文引导；
- 覆盖分子对接全过程。

当前 V0.1 是 Lite MVP，主要价值是跑通本地 PDBQT docking 闭环。它依赖用户已经准备好的 PDBQT 文件和本机 AutoDock Vina，是阶段性实现，不是最终产品形态。

V0.8 开始把“开箱即用”拆成三种可解释的使用模式：

- Basic Mode：已有 PDBQT，只需要 AutoDock Vina；
- Assisted Mode：从 raw 结构自动准备 PDBQT，需要 Python + RDKit + Meeko；
- Demo Mode：用小型示例体验流程，示例不用于科研结论。

V0.8.1 强化 Basic Mode，确保 raw 下载和自动准备不会阻塞已有 PDBQT 用户完成最低依赖 docking。

V0.8.2 新增 Demo Mode 示例项目，提供小型 Basic / Assisted 玩具数据，用于软件流程演示，不用于科研结论。

V0.8.3 升级首次启动向导，让用户先选择“已有 PDBQT / 只有 raw 文件 / 先看示例”，并基于当前工具链状态显示缺失项和下一步建议。该版本不自动安装工具，也不新增科学功能。

V0.8.4 新增工具链修复建议，把 Vina、Python/RDKit/Meeko 和 Microsoft Store Python 等问题转成可读的手动步骤和可复制命令。该版本仍不自动安装工具、不修改 PATH、不新增科学功能。

V0.8.5 新增安装后自检和本地 Markdown 诊断报告导出，帮助用户判断当前安装能完成 Basic / Assisted / Demo 哪些路径。该版本不上传诊断数据、不自动安装工具，也不改变科学流程。

V0.8.6 补充 release artifact capability profile，明确安装包包含什么、不包含什么，以及 Basic / Assisted / Demo Mode 的预期条件。该版本只整理发布材料和脚本，不生成或提交安装包。

V0.8.7 是开箱即用工作流冻结审计，统一版本号，复查 Basic / Assisted / Demo Mode 表述、发布材料和仓库卫生。该版本不新增功能，不提交安装包或运行时文件。

## V0.1: 本地 PDBQT Docking Lite MVP

目标：跑通最小闭环，证明项目、运行、解析和报告链路可用。

已完成：

- 工具检测；
- Vina / Python 路径配置；
- 创建项目；
- 导入已经准备好的 `receptor.pdbqt` 和 `ligand.pdbqt`；
- 手动设置 docking box；
- 设置 Vina 参数；
- 生成 `vina_config.txt`；
- 准备 run；
- 执行 AutoDock Vina；
- 解析 Vina log；
- 导出 `scores.csv`；
- 前端显示结果表格；
- 导出 Markdown 报告。

明确边界：

- 不内置 Vina；
- 不内置 Python 工具链；
- 不自动下载结构；
- 不自动分子格式转换；
- 不自动准备 receptor / ligand；
- 不自动药效判断；
- 不做 3D 可视化；
- 不做相互作用分析。

## V0.2: DockStart Full 基础路线

V0.2 从“用户自己安装工具”转向“DockStart 管理工具链”，同时为后续结构获取和自动准备留出清晰边界。更多工具链设计见 [toolchain_design.md](toolchain_design.md) 和 [toolchain_runtime.md](toolchain_runtime.md)。

### A. Toolchain Line

这条线只处理工具链资源、路径解析、manifest、许可证和状态展示，不等于已经实现分子准备。

#### V0.2.0: bundled Vina 路径识别，已完成

- 建立 `resources/tools/vina/` 和 `resources/licenses/`；
- 如果存在 `resources/tools/vina/vina.exe`，优先识别为 bundled Vina；
- Vina 解析优先级为 `bundled` → `configured` → `auto`；
- 不强制提交真实 `vina.exe`。

#### V0.2.1: 工具链资源路径与打包兼容，已完成

- 统一开发环境和 Tauri 打包环境中的 `resources/` 解析；
- 支持 Tauri resource dir 下的 packaged resources；
- 确保 manifest、license notes 和工具 README 能进入打包资源。

#### V0.2.2: bundled Vina 装配与许可证检查，已完成

- 新增本地 Vina 装配脚本；
- 记录 bundled Vina 的版本、来源、`sha256` 和许可证状态；
- ToolchainStatusPage 显示 Vina package ready / incomplete / missing；
- 默认不提交真实 Vina 二进制。

#### V0.2.3: bundled Python runtime resolution and integrity check，已完成

- 识别 `resources/python/python.exe`；
- Python 解析优先级为 `bundled` → `configured` → `current_environment`；
- `resources/toolchain_manifest.json` 记录 `bundled_python` 的版本、来源、`sha256` 和准备时间；
- ToolchainStatusPage 显示 bundled Python 是否存在、路径、版本、`sha256` 和当前 Python 来源；
- Meeko / RDKit 检测使用解析后的 Python；V0.2.3 阶段只做 import 检测，V0.3.1 之后增加准备能力检测；
- 当前仓库没有提交完整 Python runtime，`resources/python/` 当前只提交 `README.md`。

#### V0.2.4: 路线校准与工具链文档整理，已完成

- 明确 V0.2.3 是 runtime 解析和完整性检查，不是 RDKit/Meeko 功能接入；
- 明确 `scripts/prepare_bundled_python.py` 只复制本地 Python runtime、计算 `python.exe` sha256、读取版本并更新 manifest；
- 明确该脚本不联网、不安装 Python 包、不安装 RDKit、不安装 Meeko；
- 明确当时仍未实现 PDB/PubChem 下载、PDBQT 自动生成、RDKit/Meeko 分子处理、3D 可视化或药效判断。

#### 后续 Toolchain 方向

- 可选的离线 Python runtime 管理；
- 可选的 RDKit/Meeko 离线包状态检查；
- 更完整的工具链版本锁定、来源记录和升级策略；
- 继续默认不提交大体积二进制 runtime；
- 真正内置 RDKit/Meeko 前必须单独审查许可证、体积、更新机制和分发方式。

### B. Structure Acquisition Line

这条线处理 raw structure 获取和原始文件管理，与 Toolchain line 分开推进。

#### V0.2.5: RCSB PDB / PubChem raw 下载基础层，已完成

- 通过 PDB ID 下载受体相关 raw 结构；
- 通过 PubChem CID 下载配体 raw SDF；
- 保存到项目 `raw/` 目录；
- 记录下载来源、时间和原始文件路径；
- 不自动转 PDBQT；
- 不调用 RDKit、Meeko、Open Babel、PLIP 或 MGLTools。

#### V0.2.6: raw 文件管理增强，已完成

- `get_raw_files_status(project_dir)` 返回 receptor/ligand raw 状态；
- 状态包含 `source`、`source_id`、`raw_file`、`exists`、`size_bytes`、`modified_at`、`absolute_path` 和 `record_consistent`；
- StructureFetchPage 显示 raw 状态卡片、文件大小、修改时间和记录一致性；
- 支持清除 receptor/ligand raw 记录；
- 清除 raw 记录不会删除 prepared PDBQT 文件；
- `delete_file=True` 时只允许删除项目 `raw/` 目录下的文件；
- overwrite 默认关闭，开启时在前端显示覆盖警告。

#### V0.2.7: 结构来源查询增强，已完成

- RCSB PDB 下载支持 `pdb` 和 `cif` 两种 raw 格式；
- PubChem CID 查询保持兼容；
- PubChem name 查询保存为 `raw/ligand_name_{name}.sdf`；
- SMILES 查询返回中文结构化“暂未支持”提示；
- `project.json` 继续记录 `source`、`source_id`、`query_type`、`raw_file` 和 `downloaded_at`；
- 继续保持不自动转 PDBQT、不调用 RDKit/Meeko。

#### V0.2.8: raw/prepared 流程 UI 引导增强，已完成

- 首页显示当前推荐流程；
- ProjectCreatePage 继续提供“下载原始结构文件”和“直接导入 PDBQT”两个入口；
- ImportPdbqtPage 强调 raw 文件和 prepared PDBQT 的区别；
- StructureFetchPage 下载后提示下一步仍需手动准备 PDBQT；
- ToolchainStatusPage 在 V0.2.8 阶段明确 Meeko/RDKit 当时只做 import 检测，不会自动处理分子。

#### V0.2.9: 手动 PDBQT 准备指南，已完成

- 新增 `docs/manual_pdbqt_preparation.md`；
- 写清 raw 文件、prepared PDBQT 和 Vina 输入要求；
- 说明下载 PDB/CIF/SDF 后为什么仍不能直接运行 Vina；
- 说明可选外部工具 Meeko、AutoDockTools/MGLTools 和 Open Babel；
- 记录 Open Babel、MGLTools、PLIP 当前不内置；
- 明确 DockStart 当前不保证外部工具生成的 PDBQT 科学正确性。

#### V0.2.10: smoke test 与 release notes 整理，已完成

- 整理 V0.1 本地 PDBQT 完整流程 smoke test；
- 整理 V0.2 raw 下载流程 smoke test；
- 明确 raw 文件和 prepared 文件的预期产物；
- 明确 raw 文件不等于 prepared PDBQT；
- 明确 V0.2.10 阶段仍不自动转 PDBQT；
- 更新 release notes。

#### 延后：raw → prepared PDBQT 自动准备

- RDKit 配体处理延后；
- Meeko 受体/配体准备延后；
- PDB/SDF/MOL2 自动转 PDBQT 延后；
- Open Babel、PLIP、MGLTools 暂不接入。
- 后续 V0.3 才考虑 RDKit/Meeko 自动准备的设计、测试和许可证审查。

## V0.3: raw → prepared PDBQT 自动准备设计

计划：

- V0.3.0：准备工作流数据模型、状态检查和最小前端入口，已完成；
- V0.3.1：RDKit/Meeko 准备能力检测增强，已完成；只检测 import、版本、基础 SDF 读取和候选 Meeko API/CLI，不执行分子处理；
- V0.3.2：ligand SDF/MOL 自动准备为 `prepared/ligand.pdbqt`，已完成；默认不覆盖已有 prepared ligand，并记录 stdout/stderr/log；
- V0.3.3：receptor PDB/CIF 自动准备为 `prepared/receptor.pdbqt`，已完成；依赖可发现的 Meeko receptor CLI，并记录 stdout/stderr/log；
- V0.3.4：preparation 工作流接入现有 docking 主线，已完成；raw 存在但 prepared 缺失时，config/run 前置检查会提示先准备 PDBQT；preparation 失败时提示查看日志；不改变 Vina config、执行、解析或报告语义；
- V0.3.5：preparation 日志、审计与可复现记录，已完成；每次自动准备写入独立 `preparation/{target}_{NNN}/` 目录和 metadata/stdout/stderr/command/input/output 记录；
- V0.3.6：preparation smoke tests 与文档收尾，已完成；文档说明 raw 下载、自动准备、Box/config/run、结果解析和 Markdown 报告导出的完整路径与科学限制；
- 输入校验和错误提示继续推进；
- 许可证、体积、离线包和更新机制审查；
- mock-first 测试方案；
- 第一阶段可以只做设计文档或最小接口，不应直接扩大到复杂分子处理。

注意：

- V0.3 之前，raw 文件仍不能直接运行 Vina；
- V0.3.5 之后，ligand SDF/MOL 和 receptor PDB/CIF 可以尝试自动准备为 PDBQT，并能接回现有 config/run 流程；自动准备过程有独立审计记录；MOL2/SMILES 和复杂结构修复仍需后续阶段实现；
- 不应在缺少许可证审查、测试和用户确认的情况下自动生成 PDBQT；
- Open Babel、PLIP、MGLTools 仍暂不进入核心内置包。
- V0.4 已完成基础 3D viewer、Box overlay 和 docking pose 查看；相互作用分析、批量 docking、专业级建模检查仍放到后续版本评估。

## V0.4: 结构可视化与可视化 Box 设置

已完成：

- V0.4.0：viewer 后端数据模型与项目内结构文本读取；
- V0.4.1：最小 3Dmol.js ViewerPage，前端依赖通过 npm 管理，不使用 CDN；
- V0.4.2：Box overlay 与 `project.json.box` 保存同步；
- V0.4.3：docking pose mode 查看，并在 `scores.csv` 存在时显示 affinity / RMSD 摘要；
- V0.4.4：viewer 状态接入 workflow status；
- V0.4.5：viewer 文档与 smoke test 收尾；
- V0.4.6：viewer 冻结审计。

注意：

- 仍应保持数值参数可编辑；
- 可视化结果不应被解释为药效结论；
- V0.4 viewer 不做 PLIP/ProLIF、相互作用分析、pocket prediction、自动 Box 推荐或专业建模修复。

## V0.5: 前端工作流整改

已完成：

- V0.5.0：AppShell、Sidebar、Topbar 和共享页面组件基础；
- V0.5.1：ProjectDashboardPage 项目总览；
- V0.5.2：引导式 workflow stepper；
- V0.5.3：统一状态、warning、error、命令结果和科学边界展示组件；
- V0.5.4：StructureFetchPage 与 PreparationPage 信息层级整改；
- V0.5.5：ViewerPage 三栏工作区，整理结构加载、Box 和 pose 查看；
- V0.5.6：Vina config / prepare / execute / result / report 页面统一流程条；
- V0.5.7：HelpPage 与 onboarding；
- V0.5.8：前端工作流冻结审计。

注意：

- V0.5 只整理前端信息架构和中文引导；
- 不改变 Vina config 生成、Vina 执行、score 解析或 Markdown 报告语义；
- 不新增 PLIP/ProLIF、相互作用分析、pocket prediction、药效判断、Open Babel 或 MGLTools；
- 不使用外部 CDN，不提交大型结构文件、真实 docking 输出或 Python runtime。

## V0.6: Windows 打包与发布准备

计划：

- V0.6.0：发布工程结构与打包策略，已完成；
- V0.6.1：bundled Vina 准备与完整性检查，已完成；
- V0.6.2：Python/RDKit/Meeko 工具链分发策略与环境导出，已完成；
- V0.6.3：首次启动与工具链引导，已完成；
- V0.6.4：Windows release build 脚本，已完成；
- V0.6.5：本地安装包构建与验收记录，已完成；
- V0.6.6：GitHub Release 准备，已完成；
- V0.6.7：发布冻结审计，已完成。

注意：

- V0.6 是发布工程线，不新增科学功能；
- V0.6 可以生成本地安装包用于验收，但安装包不得提交进 Git；
- V0.6 不自动安装 RDKit/Meeko，不提交 conda env 或 Python runtime；
- V0.6 不接入 PLIP/ProLIF/Open Babel/MGLTools，不做相互作用分析、pocket prediction 或药效判断。

## V0.7: 批量 Docking 与结果管理

候选方向：

- 批量 docking；
- 多 run 结果管理；
- 项目结果索引；
- 结果排序、筛选和比较；
- 更完善的导出格式。

注意：

- 批量 docking 需要更严格的任务管理和错误恢复；
- 仍不应自动输出药效结论。
## V0.4.0 Viewer 数据模型状态补充

V0.4.0 已完成 viewer 后端数据模型与结构文件读取接口。当前能力只包括：

- 读取项目目录内的 receptor raw、ligand raw、prepared receptor、prepared ligand 和 docking output 文本结构文件；
- 列出和读取 `runs/{run_id}/out.pdbqt` 中的 docking pose 文本；
- 拒绝项目目录外路径，避免路径穿越；
- 对超过 20 MB 的结构文件返回中文结构化错误，避免前端一次性加载过大文本；
- 不调用 RDKit、Meeko 或 AutoDock Vina；
- 不做 PLIP/ProLIF、相互作用分析、pocket prediction 或药效判断。

V0.4.1 已接入最小前端 3Dmol.js ViewerPage，使用 npm 本地依赖，不使用 CDN。V0.4.2 已完成 Box overlay 数据与 `project.json.box` 保存同步。V0.4.3 已完成 docking pose mode 查看和 `scores.csv` 摘要对应。V0.4.4 已把 viewer 状态接入 workflow status，并补充 BoxSetupPage / ResultPage 的最小查看入口。V0.4.5 已完成 viewer 文档与 smoke test 收尾。
