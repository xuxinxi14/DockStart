<p align="center">
  <img src="apps/desktop/public/dockstart-icon.png" width="88" alt="DockStart 图标">
</p>

<h1 align="center">DockStart</h1>

<p align="center">
  面向 AutoDock Vina 的中文本地分子对接工作台
</p>

<p align="center">
  从结构准备、对接箱体设置和任务运行，到构象查看、结果解析与实验记录导出。
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/source-v0.14.0-155f8a">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-1f6feb">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-2f7d59">
  <img alt="Runtime" src="https://img.shields.io/badge/runtime-local--first-314d67">
</p>

<p align="center">
  <a href="https://github.com/xuxinxi14/DockStart/releases">下载</a>
  · <a href="docs/user_guide.md">使用指南</a>
  · <a href="docs/faq.md">常见问题</a>
  · <a href="CHANGELOG.md">更新记录</a>
  · <a href="docs/license_notes.md">第三方许可证</a>
</p>

---

DockStart 是一个基于 [AutoDock Vina](https://vina.scripps.edu/) 的第三方开源桌面应用。它不开发新的 docking 算法，而是把分散的命令行步骤整理成清晰、可追踪的中文工作流，帮助初学者减少格式、路径、参数和结果归档方面的错误。

> 当前源码与本地 Windows 候选包版本为 **v0.14.0**。AutoGrid4 仍是用户自行安装的 GPL 外部工具，不包含在 Basic 或 Assisted 安装包中。安装包不提交到 Git 仓库，请以 [GitHub Releases](https://github.com/xuxinxi14/DockStart/releases) 中实际发布的版本、门禁结果和校验值为准。

> v0.14.0 在标准 AutoDock4 maps 协议中补齐有限柔性单配体、刚性串行批量和刚性双配体共同对接。批量队列复用一组冻结 maps，但每个配体独立运行和排名；共同对接则让两个配体在一次 Vina 搜索中产生联合评分，不能拆成两个成员 affinity。两条刚性多配体路径都会先验证 maps 覆盖全部配体原子类型。AD4Zn beta 与水合 AD4 Experimental 仍是隔离的单配体子协议，不能与这些标准 AD4 扩展组合。

## 为什么使用 DockStart

- **完整工作流**：在同一个项目中完成输入准备、Box、Vina 参数、运行、构象查看和报告导出。
- **中文引导**：解释每一步要做什么、为什么要做，以及阻塞时应该检查什么。
- **本地优先**：对接、结构准备、项目记录和诊断均在本机执行；只有主动使用 RCSB/PubChem 下载时需要联网。
- **可复现**：保存输入快照、配置、命令、工具版本、stdout/stderr、结果、时间和 SHA256。
- **不隐藏科学边界**：自动准备、Box 定位和 docking score 都需要人工判断，不会被描述成真实结合或药效证明。

## 选择安装版本

DockStart 提供两个 Windows x64 发布 profile。二者使用同一个应用身份，请勿并行安装。

| | Basic Stable | Assisted Stable |
| --- | --- | --- |
| 适合谁 | 已有受体和配体 PDBQT | 只有受体 PDB/CIF 与配体 SDF/MOL/MOL2 |
| 内置 AutoDock Vina | 是，1.2.7 | 是，1.2.7 |
| 内置后端 Python | 是，精简运行时 | 是，独立 CPython 3.11 运行时 |
| 内置 RDKit / Meeko | 否 | 是，RDKit 2026.3.3 / Meeko 0.7.1 |
| PDB/SDF/MOL/MOL2 → PDBQT | 不提供 | 可离线尝试准备 |
| PDBQT 对接完整流程 | 支持 | 支持 |
| AutoDock4 maps 工作流 | 支持，需外部 AutoGrid4 | 支持，需外部 AutoGrid4 |
| 典型安装包体积 | 较小 | 较大 |

如果不确定：

- 已经有 `receptor.pdbqt` 和 `ligand.pdbqt`：选择 **Basic Stable**。
- 只有 `.pdb`、`.cif`、`.sdf`、`.mol` 或单分子 `.mol2`：选择 **Assisted Stable**。
- 只想了解软件流程：安装任一版本后打开内置示例。

> 当前安装包尚未进行 Authenticode 签名，Windows SmartScreen 可能显示“未知发布者”。发布者字段应为 `XinXi Xu`，安装前仍应核对 Release 页面提供的 SHA256。

## 六步完成一次对接

```text
创建或打开项目
      ↓
导入已有 PDBQT，或在 Assisted 中从 raw 文件准备 PDBQT
      ↓
检查受体、配体和对接箱体
      ↓
设置 Vina 参数并通过运行前检查
      ↓
执行 AutoDock Vina
      ↓
查看构象与 scores，导出 Markdown 实验记录
```

1. **创建项目**：选择本地目录，DockStart 建立独立的项目文件结构。
2. **准备输入**：Basic 直接导入受体/配体 PDBQT；Assisted 可搜索或导入 PDB/CIF、SDF/MOL/MOL2，并在写入项目后尝试生成 PDBQT。
3. **设置搜索范围**：在 3D 工作台检查结构和 Box，输入中心及尺寸；可按受体坐标范围快速定位，再人工微调。
4. **配置运行**：设置搜索彻底程度、构象数量、能量范围、CPU 和随机种子。
5. **开始对接**：运行前检查会确认项目文件、PDBQT、Box、Vina 参数、工具和输出目录。
6. **查看结果**：比较 pose、affinity 与 RMSD，查看输出文件并导出 Markdown 报告。

高级用户可在对接工作台切换到独立的 **AutoDock4（maps）** 协议，生成或导入 affinity maps 后运行。标准 AD4 支持刚性单配体、有限柔性单配体、刚性串行批量，以及刚性双配体共同对接；所有路径都会冻结并校验实际使用的 maps。该协议的 scores 与报告独立保存，不能与 Vina/Vinardo 分值直接比较。AutoGrid4 需要用户自行安装并在设置页配置。

> 当前候选包同时保留 **Vina / Vinardo 预计算 maps 复用**。它使用现有 AutoDock Vina 的 `--write_maps` 生成网格，或导入带 DockStart manifest 的 maps；不依赖 AutoGrid4，也不是 AutoDock4 评分。启用后，run 使用冻结的 `--maps`，不再向 Vina 传入 `--receptor`、Box 或 spacing，因此属于 `grid-only`，等价于 `no-refine`。该模式仍只允许刚性受体、单配体、全局对接，并把评分函数、受体、请求 Box、实际网格、Vina 二进制和每个 map 的 SHA256 一起绑定。

> 当前候选包保留显式标记的 **AD4Zn beta**。它不是新的 Vina 评分函数，而是先为符合条件的三配位 Zn 受体生成 TZ 几何伪原子，再使用用户提供的 `AD4Zn.dat` 和 AutoGrid4 4.2.7+ 生成专用 maps，最后由 Vina 以 `--maps ... --scoring ad4` 运行。协议只适用于单核 Zn 位点，不自动泛化到多核位点或 Mg、Fe、Ca 等其他金属；没有生成 TZ、缺少参数文件或 AutoGrid 版本不满足时均阻止运行，不降级为标准 AD4。`AD4Zn.dat` 文件自身声明 GPL-2.0-or-later，DockStart 不随仓库或安装包内置、也不自动下载；用户明确选择后，应用会为可复现性复制到项目并记录本机来源路径、SHA256、许可证 ID、受支持参数配置和固定上游参考。分享含该副本的项目时，分享者需要自行履行相应 GPL 再分发义务。它仍是 Beta，不代表已进入正式 Release。

Box 的“定位到受体”只使用受体原子坐标范围的几何中心，不预测结合口袋，也不会自动判断 Box 是否适合研究目标。

详细操作见 [用户指南](docs/user_guide.md)。需要逐项复现官方教程时，使用 [v0.13.8 六个 AutoDock Vina 官方示例人工验收清单](docs/manual_official_vina_examples_v0_13_8.md)。如果第一次使用 AutoDock Vina，建议先从 [示例项目](docs/demo_projects.md) 开始。

> 当前候选包包含 `score_only` 与双阶段 `local_only`：前者评价冻结的输入姿势，后者在同一 run 内先记录输入评分，再执行局部优化，显示“优化后－输入”差值、未对齐重原子位移，并可在同一受体坐标系中叠合输入与优化后姿势。帮助页、新建项目页和项目总览已提供评分/局部优化入口，文件来源与科学任务分开选择；独立 PubChem 在线构象不作为评价输入入口。评价模式运行前必须由用户在同场 3D 视图确认当前姿势；确认绑定本次运行实际使用的刚性受体、可选柔性侧链与配体 PDBQT 的 SHA256，任一输入文件替换后失效，prepared run 执行前还会再次核对确认与不可变输入快照。该记录只表示用户完成复核，不代表软件已验证姿势。叠合读取本次 run 的冻结受体和两份姿势，不读取后来替换的项目当前结构；现代双阶段 run 在显示前复核关键文件 SHA256。单次运行还可设置 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine`、`force_even_voxels`，并在刚性单配体 `score_only` 中选填 `unbound_energy`。有效值和 Vina 能力证据会冻结到 run 快照；两个专家开关默认关闭，未结合态参考能量默认留空。需要门禁的选项只有在当前 Vina 的高级帮助明确声明支持且版本满足要求时才允许运行；评价模式的 `autobox` 要求稳定版 Vina 1.2.3 或更高版本，`unbound_energy` 最低门槛按 SemVer 与稳定版 Vina 1.2.4 比较，因此 `1.2.4-rc1` 不通过。新生成的 `score_only` 结果会先核对记录的日志 SHA256；显式未结合态参考还会核对日志第 (4) 项与冻结值，并验证总评分满足 `(1) + (2) + (3) - (4)`。生成后的 `evaluation.json` 也以 SHA256 绑定到本次 run，读取与报告前会再次校验。AutoDock4 maps、柔性受体、全局对接、局部优化和批量筛选不使用 `unbound_energy`。这些能力已进入 v0.14.0 本地候选包，但尚未完成正式 Release 的安装态门禁。

> 串行批量筛选结果工作区可检索、筛选、排序和分页全部配体，成功项按需加载本次筛选冻结的受体与最佳构象，并在新记录中核对输出 SHA256；终态队列可生成独立的 `screening_report.md`。批量全局对接还会继承 `max_evals`、`min_rmsd`、`spacing`、`verbosity`、`no_refine` 和 `force_even_voxels`：这些值进入队列冻结状态、每个配体的独立配置、整批报告和归档协议指纹；建队后界面持续显示本队列冻结的 Box 与 Vina 快照。`no_refine` 与 `force_even_voxels` 仍受当前 Vina 运行时能力门禁；能力在开始或恢复队列时统一复核。每次 attempt 会先把冻结受体和当前配体按实际字节写入独立目录，再核对输入、配置及 Vina 二进制的大小与 SHA256；Vina 返回后还会再次复核这些证据，全部一致才把该 attempt 标记为成功。证据写入 `attempt.json`，归档详情和双归档比较也会复核已保存的 attempt 输入与配置；Vina 二进制本身不复制进归档，只保留并交叉核对冻结的版本、大小和 SHA256。旧归档缺少逐次运行证据时仍可读取，但归档详情会明确提示不能视为完整验证。`unbound_energy` 只适用于刚性单配体 `score_only`，不会进入批量队列。归档后的筛选可从历史列表重新打开并只读查看结果与报告，成功配体可按 `archive_id` 安全加载 Mode 1；损坏归档仍保留在列表中，但会阻止打开详情。旧归档缺少输出哈希时会显示“未完全验证”警告。历史列表还可严格只读比较恰好两个不同的有效归档：先选归档作为基线，后选归档作为对照，逐配体只按冻结输入的 SHA256 匹配。比较前会实际核对两批受体和全部配体输入的文件大小与 SHA256，并比较覆盖受体、评分函数、Box 六项、Vina 版本与二进制 SHA256、批量基础参数及上述六项适用高级参数的协议指纹。只有协议相同、配体身份唯一、两侧运行成功且评分和排名有效时，才显示“对照－基线”差值；协议不同仍可并排查看但不计算差值，同一归档内重复 SHA256 的配体标记为 `ambiguous`。归档浏览和比较全程只读，不能恢复活动队列、编辑、重试或修改项目及归档。该工作区已进入 v0.14.0 本地候选包，正式 Release 能力仍以发布页为准。
>
> 新建队列还会冻结并哈希九项资源限制。开始、恢复、读取归档和比较归档时都会重新核对重试次数、Top N、配体数量、单文件大小、总输入大小以及 CPU、Box 和搜索参数边界；超过应用硬上限或与冻结哈希不一致时，在启动 Vina 前阻断。旧记录缺少资源限制或哈希时按默认上限只读兼容，并标记为推断或未完全验证。
>
> 配体库导入已支持多文件、目录递归和多分子 SDF 逐记录处理。每条 SDF 记录保留原始 1-based 位置；单条 RDKit/Meeko 错误会显示在预览中，不会吞掉其他有效分子。准备后的 PDBQT 按精确字节 SHA256 去重，重复与失败项不能进入队列；用户可在建队前逐条选择可用项。源文件、record SHA256 和重复来源会写入 staging index，并在创建队列时校验后冻结到任务与 attempt。当前尚未冻结原始 record 拓扑字节，因此不会据此生成批量 SDF。该能力已纳入 v0.14.0 本地候选包，不改变项目 schema。
>
> 有效的历史筛选归档现在可另存为单个 ZIP。包内根清单 `dockstart_screening_export.json` 记录逐文件大小与 SHA256、payload 树哈希和源归档完整性结论；DockStart 写完后会重新核对 ZIP 的 CRC、成员集合和逐文件哈希。现代记录还会交叉核对 `attempt.json` 与冻结状态，并为完整汇总、Top N 和 Markdown 实验记录保存大小与 SHA256；旧归档缺少这些历史凭据时仍可导出，但会标为“部分验证”，不会把导出时新计算的哈希冒充历史证据。为避免保存对话框与实际写入之间的覆盖竞态，GUI 不替换已有目标；路径已被占用时会保留原文件并要求另选名称。导出只读取归档，不修改项目或归档，也不包含 Vina 可执行文件、活动队列、项目当前状态或 staging 文件。该 ZIP 是便于移动和审计的只读实验包，不是可直接恢复运行的项目备份，也不是数字签名；已有 JSON 可能保留本机绝对路径，因此导出内容默认不匿名。该增量同样不修改版本号或现有安装包。

## 当前能力

### 项目与工具链

- 创建、打开和迁移 DockStart 项目；
- 检测随附、用户配置和系统 PATH 中的工具；
- 区分 Basic、Assisted 与 Demo 可用状态；
- 导出本地诊断报告；
- 按运行时 fingerprint 缓存工具检测，支持显式重新检测。

### 结构准备

- 导入已有 receptor/ligand PDBQT；
- 按 PDB ID 或关键词搜索 RCSB 候选，设置返回数量并逐项只读 3D 预览；
- 按 PubChem CID 或名称搜索配体候选，不默认选择第一项；
- 明确选择候选后下载并自动准备；本地导入 PDB/CIF、SDF/MOL 后同样立即尝试准备；
- Assisted 使用独立 RDKit/Meeko 工具链尝试准备 PDBQT；
- CIF 受体通过随附 Gemmi 转为经审计的中间 PDB，再交给 Meeko，避免依赖未随包提供的 ProDy；
- 保存每次 preparation 的参数、输入快照、stdout、stderr、metadata 和输出检查。

### 3D 对接工作台

- 同时查看受体、配体、对接结果与 Box；
- 编辑 `center_x/y/z` 和 `size_x/y/z`；
- 用鼠标滚轮绑定并调整单个 Box 参数；
- 快速定位到受体坐标范围中心，并恢复进入页面时的参数；
- 调整 Box 线宽、XYZ 坐标轴显示与轴间距；
- 设置基础 Vina 参数，以及源码未发布的评估上限、构象最小间距、网格间距、日志详细程度、网格评分精修、偶数体素开关和仅评分模式的显式未结合态参考能量；串行批量全局对接继承前六项适用高级参数，不继承显式未结合态参考能量；
- 可为 Vina/Vinardo 生成、导入、校验和复用预计算 maps；maps 模式明确标记为刚性单配体全局对接与 `grid-only / no-refine` 语义；
- 源码未发布的 AD4Zn beta 可对满足三受体配位与开放四面体方向条件的 Zn 位点生成 TZ，使用外部 AutoGrid4 4.2.7+ 与用户提供的 `AD4Zn.dat` 生成专用 maps；
- 源码未发布的多配体共同对接实验协议可让恰好两个已准备 PDBQT 在同一次 Vina/Vinardo 全局搜索中联合运行；它不自动替代串行批量筛选；
- 执行运行前检查、任务进度显示和取消操作。

### 结果与可追溯性

- 解析 Vina affinity 与 RMSD 表；
- 按 mode 查看 docking pose；
- 源码未发布增量可独立执行输入姿势评分和局部优化；新的局部优化 run 保存两阶段命令、日志、评分和关键文件 SHA256；
- 局部优化结果按严格原子身份报告同一受体坐标系内的未对齐重原子 RMSD、平均/最大位移与质心位移，不把它解释成共晶验证 RMSD；
- 现代双阶段局部优化结果可同时显示冻结的输入姿势与优化后姿势，以青色细棒和橙色粗棒双重区分；叠合不做额外刚体对齐；
- 串行批量筛选支持多文件/目录、多分子 SDF 逐记录预览与勾选；可查看完整结果、Top N、单项失败诊断和逐配体最佳构象，并导出整批 Markdown 实验记录；历史归档可只读浏览；
- 多配体共同对接把每个 Mode 解释为两个成员组成的一组联合构象，只显示该联合体系的一个评分；不拆出或推断成员 affinity，也不直接比较成员数或组成不同的联合任务；
- 导出 `scores.csv` 与 Markdown 实验记录；
- 保存输入、输出、工具二进制 SHA256；
- 使用原子写入、revision 冲突检测和 schema migration 保护项目数据；
- 对异常中断的 preparation/run 做保守状态恢复。

## 支持范围

| 类型 | 当前支持 | 说明 |
| --- | --- | --- |
| Vina 输入 | PDBQT | Basic 与 Assisted 均支持 |
| 受体 raw | PDB、CIF | Assisted 可尝试准备 PDBQT |
| 配体 raw | SDF、MOL | Assisted 可尝试准备 PDBQT |
| 结构搜索与下载 | RCSB PDB ID/关键词、PubChem CID/名称 | 候选预览与下载需要网络；不会默认选择首项 |
| 3D 查看 | PDB、PDBQT、CIF、SDF、MOL 等 | 取决于 3Dmol.js 对格式的解析能力 |
| 对接输出 | PDBQT、CSV、Markdown | pose、scores 与实验记录 |
| 批量筛选 | 多个文件、目录或多分子 SDF，串行队列 | record 级导入预览、内容去重、完整 CSV、Top N、逐配体构象、Markdown 实验记录与只读归档历史 |
| 多配体共同对接 | 源码实验性：恰好两个已准备 PDBQT | Vina 1.2.0+；刚性受体、Vina/Vinardo、全局对接；一个 Mode 是一组联合构象且只有联合评分 |
| 预计算网格 | AutoDock4 maps；源码另含 Vina/Vinardo maps | 两类 maps 使用不同评分协议、manifest 和科学解释，不能混用 |

当前不提供：

- MOL2/SMILES 自动准备；
- 复杂受体修复、可靠的质子化/电荷判断或自动链选择；
- pocket prediction 或真实结合位点识别；
- PLIP/ProLIF 相互作用分析；
- Open Babel、MGLTools 内置分发；
- 多配体共同对接与柔性受体、AD4/AD4Zn/maps、`score_only`、`local_only` 或水合对接的组合；
- 分子动力学、PDF 报告或 AI 药效判断；
- 对 AutoDock Vina 算法或 scoring function 的修改。

## 项目输出

一个典型项目会形成以下结构：

```text
my_project/
├─ project.json
├─ raw/
│  ├─ receptor.pdb
│  └─ ligand.sdf
├─ prepared/
│  ├─ receptor.pdbqt
│  └─ ligand.pdbqt
├─ preparation/
│  ├─ receptor_001/
│  └─ ligand_001/
├─ configs/
│  └─ vina_config.txt
├─ runs/
│  └─ run_001/
│     ├─ metadata.json
│     ├─ config_snapshot.txt
│     ├─ stdout.txt
│     ├─ stderr.txt
│     ├─ log.txt
│     ├─ out.pdbqt
│     ├─ scores.csv
│     └─ docking_report.md
├─ results/
│  └─ scores.csv
└─ reports/
   └─ docking_report.md
```

项目记录采用相对路径，便于整体移动和归档。输入、输出和工具来源会写入 metadata；分享项目之前，请检查其中是否包含不希望公开的本机路径或研究数据。

## 示例项目

安装包内包含三类小型示例：

- `basic_pdbqt`：体验已有 PDBQT 的最小对接流程；
- `assisted_raw`：体验 PDB + SDF 的结构准备流程；
- `viewer_result`：直接查看已完成的 pose、score 和报告。

示例只用于软件回归和操作教学，不应作为科研结论。详见 [示例项目说明](docs/demo_projects.md)。

## 从源码运行

### 环境

- Windows 10/11 x64；
- Node.js 与 npm（建议使用当前 LTS）；
- Rust stable 与 Tauri Windows 构建依赖；
- Python 3.11+。

### 开发启动

```powershell
git clone https://github.com/xuxinxi14/DockStart.git
cd DockStart\apps\desktop
npm ci
npm run tauri dev
```

只启动前端界面：

```powershell
cd apps\desktop
npm run dev
```

源码运行不会自动下载或安装 AutoDock Vina、RDKit 或 Meeko。请在设置页配置工具路径，或按发布文档准备仓库外的本地资源。

## 测试与构建

在仓库根目录执行：

```powershell
# 后端测试
python -m unittest discover -s backend/tests

# 前端生产构建
cd apps\desktop
npm run build
cd ..\..

# Rust/Tauri 检查
cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml
```

生成 Windows 发布候选：

```powershell
# 已有 PDBQT 的精简包
powershell -ExecutionPolicy Bypass -File scripts\build_windows_release.ps1 -Profile Basic

# 带离线 RDKit/Meeko 的辅助包
powershell -ExecutionPolicy Bypass -File scripts\build_windows_release.ps1 -Profile Assisted
```

Assisted 构建依赖维护者事先准备的固定离线 wheelhouse 与对应源码归档；这些大型资源不提交到 Git，发布构建本身不会联网。构建、安装态门禁和校验要求见 [Windows 打包说明](docs/release/windows_packaging.md)、[Assisted Stable 说明](docs/release/assisted_stable.md) 与 [发布检查表](docs/release/release_checklist.md)。

## 仓库结构

```text
DockStart/
├─ apps/desktop/            # Tauri + React + TypeScript 桌面端
├─ backend/adapters/        # Vina、Python、RDKit、Meeko、Viewer 适配器
├─ backend/dockstart_core/  # 项目、准备、运行、结果、诊断与持久化
├─ backend/tests/           # 后端测试
├─ resources/               # 示例、工具清单与许可证资源
├─ scripts/                 # 工具链装配、校验与 Windows 发布脚本
├─ docs/                    # 用户、设计、架构、许可与发布文档
├─ examples/                # 开发与 smoke test 示例
├─ PROJECT.md               # 产品范围和科学边界
└─ CHANGELOG.md             # 版本更新记录
```

## 文档

- [用户指南](docs/user_guide.md)
- [常见问题](docs/faq.md)
- [示例项目](docs/demo_projects.md)
- [手动准备 PDBQT](docs/manual_pdbqt_preparation.md)
- [工具链故障排查](docs/toolchain_repair_guide.md)
- [Smoke test](docs/smoke_test.md)
- [路线图](docs/roadmap.md)
- [工具链架构](docs/toolchain_design.md)
- [发布能力档案](docs/release/release_artifact_profile.md)
- [更新记录](CHANGELOG.md)

## 反馈与贡献

如果遇到问题，请在 [GitHub Issues](https://github.com/xuxinxi14/DockStart/issues) 中提供：

- DockStart 版本与安装 profile；
- Windows 版本；
- 发生问题的工作流步骤；
- 页面中的中文错误码和建议；
- 脱敏后的诊断报告、日志或最小复现项目。

请不要公开未脱敏的本机路径、私有结构文件或研究数据。提交代码前应至少运行后端测试、前端生产构建和 `cargo check`，并保持外部科研工具通过 adapter 调用。

## 许可证

DockStart 自有代码以 [Apache License 2.0](LICENSE) 发布。安装包中的第三方组件继续适用各自许可证：

- AutoDock Vina：Apache-2.0；
- RDKit：BSD-3-Clause；
- Meeko：LGPL-2.1；
- 3Dmol.js：BSD-3-Clause；
- Tauri、React 及其他依赖：见随包 notices。

Meeko 在 Assisted 中作为独立、可替换的 Python 包分发，并附带对应版本许可证、源码获取材料和第三方声明。完整边界见 [第三方许可证说明](docs/license_notes.md) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 科学免责声明

DockStart 输出的 docking score 只表示特定输入结构、对接箱体、参数和 AutoDock Vina 版本下的计算结果。自动准备结果仍需人工检查质子化、电荷、构象、缺失残基、水分子、金属、辅因子和链选择。

**Docking score 仅供结构结合趋势参考，不能替代实验验证，也不能证明真实结合、药效、安全性或临床价值。**

---

<p align="center">
  Maintained by <strong>XinXi Xu</strong>
</p>
