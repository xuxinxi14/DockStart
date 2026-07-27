# DockStart v0.12.0 阶段性手动验收手册

本手册用于在 Windows 10/11 x64 上手动验证 DockStart 的**实际可用能力**。它把“代码存在”“开发目录通过”“真实安装包通过”和“科学案例表现合理”分开记录，避免仅凭界面可点击或单元测试通过就作出过强结论。

适用版本：DockStart `v0.12.0`。开始前请在“帮助”或“工具链状态”页确认实际运行版本；若不是此版本，请在记录中注明版本和安装包 SHA256。

## 1. 验收结论规则

每个案例只能填写以下四种状态：

| 状态 | 含义 |
| --- | --- |
| 通过 | 所有步骤和通过条件均满足，证据已归档。 |
| 失败 | 出现任一失败条件，保留项目和日志，不覆盖现场。 |
| 阻塞 | 缺少指定安装包、外部工具或合法输入，尚未开始有效测试。 |
| 不适用 | 当前 profile 明确不提供该能力；这不是软件失败，但必须记录原因。 |

不要将不同协议的评分横向比较：`Vina`、`Vinardo` 和 `AutoDock4 (maps)` 的分值只能在同一协议、同一输入和同一参数内比较。所有 docking score 仅作结构结合趋势参考，不能替代实验验证。

## 2. 当前版本的能力边界

| Profile / 协议 | 本轮应验证的能力 | 不应期待的能力 |
| --- | --- | --- |
| Basic Stable | 已准备 PDBQT 的单配体 Vina/Vinardo 对接、结果、报告、批量 PDBQT、导入的 AD4 maps 运行。 | 随包 RDKit/Meeko、raw PDB/CIF/SDF/MOL 自动准备、柔性受体自动准备、大环 `mk_export`、默认 RMSD。 |
| Assisted Stable | Basic 全部能力，以及 PDB/CIF + SDF/MOL 到 PDBQT 的最小准备、RMSD、柔性受体和大环准备。 | MOL2/SMILES 自动准备、复杂受体修复、自动确认质子化/电荷/链选择。 |
| AutoDock4 maps | 标准非金属、刚性受体、单配体的 GPF/maps、`--maps --scoring ad4` 运行。 | AD4Zn、柔性受体 AD4、批量 AD4、水合对接；AutoGrid4 不随包提供。 |

截至本手册编写时，Basic 已有打包后真实 Vina 回归；Assisted 的开发和打包后离线准备/对接已通过，但仍应在干净 Windows 账户或设备完成真实安装、运行与卸载验收。二级能力（批量、有限柔性侧链、大环）必须以本手册中的真实工具链结果为准，不能仅引用源码测试。

## 3. 测试前准备

### 3.1 环境矩阵

至少准备下列环境。不要将不同环境的结果合并成一条结论。

| 环境 ID | 环境 | 必测阶段 |
| --- | --- | --- |
| E1 | Basic 干净安装 | S0、S1、S2、S4、S8；若有外部 AutoGrid4，再测 S7。 |
| E2 | Assisted 干净安装 | S0–S8 全部适用项。 |
| E3 | 源码开发环境 | 仅用于定位失败原因；不能替代 E1/E2 的安装包结论。 |

每个环境分别在以下三个项目父目录中至少执行一次 S1；S2、S4 和 S7 至少各使用第二或第三条路径一次：

```text
C:\DockStartAcceptance\case01
C:\分子对接 验收\案例 01
C:\DockStartAcceptance\非常长的项目路径\包含空格和中文字符\case01
```

测试开始前关闭其他 DockStart 实例。对于“干净安装”验收，安装前不能保留旧版 DockStart 的安装目录或卸载注册项；不要用覆盖安装代替干净安装。

### 3.2 测试数据与目录

建立一个不覆盖原始数据的根目录，例如：

```text
C:\DockStartAcceptance\
├─ input_original\                 # 只读保存下载或外部准备的原始输入
├─ 01_basic\
├─ 02_standard_1iep\
├─ 03_batch_1iep\
├─ 04_flexible_1fpu\
├─ 05_macrocycle_bace1\
├─ 06_ad4_1dwd\
└─ evidence\
```

建议数据集如下。玩具示例只能验证软件流程，不能用于科学判定。

| 数据集 | 用途 | 最低输入要求 |
| --- | --- | --- |
| `basic_pdbqt` 示例 | S1 流程冒烟 | 随软件提供的示例。 |
| 1IEP / STI（伊马替尼） | S2、S3、S4 | 同一准备版本的 receptor PDBQT、ligand PDBQT；Assisted 可另保留 PDB/SDF。 |
| 1FPU / 1IEP ligand | S5 | 原始受体 PDB、同坐标系配体、`A:315` 的 Thr315。 |
| AutoDock Vina 官方 BACE_1 大环示例 | S6 | `backend/tests/fixtures/scientific/macrocycle_bace1` 中的 receptor PDB、原始 MOL2、受控派生 SDF 和参考 PDBQT。 |
| Scripps AutoDock 4.2.6 `1dwd` 示例 | S7 | `1dwd_rec.pdbqt`、`1dwd_lig.pdbqt`，以及用户自行安装的 `autogrid4.exe`。 |

为每个原始输入记录来源、下载日期、准备工具和 SHA256。PowerShell 示例：

```powershell
Get-FileHash -Algorithm SHA256 .\input_original\* | Format-Table -AutoSize
```

### 3.3 每个案例必须保存的证据

在 `evidence\<环境 ID>\<案例 ID>\` 中保存：

- 环境信息：Windows 版本、DockStart 版本、安装包 SHA256、profile；
- 工具链状态截图：Vina、Python、RDKit、Meeko、AutoGrid4（如适用）的路径与版本；
- 操作前和操作后 `project.json`；
- 运行目录内的 `metadata.json`、配置、stdout、stderr、`log.txt`、输出 PDBQT 和 score CSV；
- 导出的 Markdown 报告；
- 关键 UI 截图：参数、运行结束、结果表、错误提示或阻断提示；
- 若有 CLI 对照：CLI 命令、stdout/stderr、输出文件 SHA256 和分数表。

不要把本机用户目录、许可证密钥或与验收无关的个人文件一起提交到证据包。

## 4. 执行顺序

按 `S0 → S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8` 执行。S5、S6、S7 是条件阶段：缺少 Assisted 或 AutoGrid4 时填写“阻塞”或“不适用”，不可伪造通过。

| 阶段 | 名称 | 通过后可得出的结论 |
| --- | --- | --- |
| S0 | 安装、工具链和路径 | 当前 profile 的工具解析和基本启动正常。 |
| S1 | Basic 最小闭环 | 已准备 PDBQT 的 Vina 对接、结果和报告能完成。 |
| S2 | 1IEP 标准回归与 CLI 对照 | DockStart 未在 Vina 参数、输入或结果解析中引入偏差。 |
| S3 | Vinardo 与参考 RMSD | 协议隔离正确；RMSD 有效或被安全拒绝。 |
| S4 | 批量筛选 | 串行队列、重试、取消、恢复和 CSV 可用。 |
| S5 | 有限柔性侧链 | Assisted 能安全准备并运行 rigid/flex 三件套。 |
| S6 | 大环准备与安全导出 | Assisted 能保存大环协议并避免不可信 SDF。 |
| S7 | AutoDock4 maps | 外部 AutoGrid4 与随包 Vina 的 maps 主链可复现。 |
| S8 | 稳定性、重启和归档 | 任务历史、恢复和路径处理不会破坏已完成结果。 |

## S0：安装、工具链与路径

### 步骤

1. 在 E1 或 E2 完成安装，启动 DockStart，确认版本为 `0.12.0`。
2. 打开“工具链状态”，截图并记录 Vina、Python、RDKit、Meeko、3D 预览器的状态、版本和路径来源。
3. E1 的预期是 Vina 正常、RDKit/Meeko 未随包提供；E2 的预期是 Vina、Python、RDKit、Meeko 均正常。
4. 如测试 S7，先自行安装 AutoGrid4 4.2.6，在“设置 → 工具路径”选择 `autogrid4.exe`，返回工具链页确认检测成功。
5. 在三个测试路径中分别创建空项目；确认创建成功、路径显示正确、重开项目后仍可识别。

### 通过条件

- 未出现崩溃、空白页、无法写入项目目录或未经解释的英文异常；
- Basic 不把缺少 RDKit/Meeko 错误写成“Vina 不可用”；
- Assisted 不要求联网安装 Python 包；
- AutoGrid4 缺失时，AD4 maps 流程给出中文配置指引，而非静默失败。

## S1：Basic 已准备 PDBQT 最小闭环（E1、E2 必测）

### 步骤

1. 复制 `basic_pdbqt` 示例，或导入自己已准备的 receptor/ligand PDBQT。新建项目，避免使用仓库模板目录直接运行。
2. 在对接工作台确认受体和配体均为 PDBQT，设置 Box；记录六个数值。
3. 设置 `scoring=vina`、固定 seed、`cpu=1`、`exhaustiveness=8`、`num_modes=3`、`energy_range=3`。
4. 保存参数，创建并执行一次运行。
5. 在结果页确认输出构象、评分表和日志均可打开；选择至少两个 pose 检查 3D 预览不会崩溃。
6. 导出 Markdown 报告，检查其项目名称、输入文件、Box、Vina 版本、参数、运行时间、评分表和科学免责声明。

### 通过条件

- run 状态为 `finished`，退出码为 0，输出 PDBQT 非空；
- `metadata.json`、`stdout`、`stderr`、`log.txt`、配置快照、`scores.csv`、报告均存在；
- pose 数量为 `1..num_modes`，评分从更负到更正排序；
- 修改项目参数后，旧 run 的报告和 metadata 不被改写。

> 玩具示例的分值不用于科学判定。S1 只验证工作流完整性。

## S2：1IEP 标准刚性对接与 CLI 一致性（E1、E2 必测）

### 建议参数

```text
scoring = vina
center = 15.190, 53.903, 16.917
size = 20, 20, 20
exhaustiveness = 32
num_modes = 9
energy_range = 3
cpu = 1
seed = -1622165383
```

### 步骤

1. E2 可从 1IEP PDB + STI SDF 尝试准备 PDBQT；完成后人工检查准备记录。E1 必须导入**同一份** prepared PDBQT，不要求也不应尝试自动准备。
2. 依次运行 DockStart，保存 run 目录全部内容。
3. 用工具链页显示的**同一个 Vina 可执行文件**和 run 记录中冻结的输入、Box、参数执行一次直接 CLI。优先复制 `metadata.json` 的 `command` 数组；仅将输出路径改至新目录，不能增删参数。
4. 保存 CLI 的 stdout/stderr、输出 PDBQT 和分数表。将 DockStart 与 CLI 的模式数、每个 affinity、最佳 affinity 比较。
5. 检查 mode 1 和至少两个候选 pose 位于 STI 共晶口袋附近，而不是明显漂到受体外。

### 通过条件

- DockStart 和 CLI 的最佳 affinity 差异不超过 `0.01 kcal/mol`；模式数与分数表一致；
- 运行中记录的 receptor、ligand、Vina 二进制和 config SHA256 与 CLI 使用对象一致；
- 作为科学参考而非工程硬门槛：若准备版本、质子化和参考坐标一致，最佳 Vina affinity 通常可落在约 `-12.5` 至 `-13.8 kcal/mol`；明显偏离时先检查输入准备，不要立刻判为运行器缺陷。

### 失败定位

若 CLI 与 DockStart 不一致，先比较：输入快照 SHA256、Vina SHA256、`scoring`、Box、seed、CPU、`exhaustiveness` 和工作目录；不要仅比较 UI 上当前显示的项目参数。

## S3：Vinardo、参考 RMSD 与错误路径

### S3.1 Vinardo（E1、E2）

1. 复制 S2 项目，保持输入和 Box 完全不变，只改为 `scoring=vinardo`。
2. 重建运行并完成一次 DockStart/CLI 对照。
3. 查看旧 Vina run 与新 Vinardo run 的报告和历史记录。

通过条件：DockStart 与 CLI 分数表一致；报告和结果页明确为 Vinardo；旧 Vina run 未被覆盖；界面不把 Vina 与 Vinardo 合并排序。

### S3.2 共晶参考 RMSD（E2 必测；E1 条件测试）

1. 选择与 DockStart 输出处于同一受体坐标系、相同化学实体的共晶参考配体。
2. 在结果页为 mode 1 计算重原子、对称性修正 RMSD，保存结果和 metadata。
3. 分别尝试：重原子数不同、化学连接不同、坐标系明显不同的参考文件。

通过条件：有效参考能产生 RMSD 和参考文件 SHA256；三种错误文件都不产生伪造的 `0.000 Å`，且保留原 docking 结果。E1 未配置 RDKit 时，预期为中文“RDKit 不可用”提示，记录为“不适用”而不是失败。

## S4：批量 PDBQT 筛选（E1、E2）

批量功能仅适用于刚性受体、Vina/Vinardo 和 PDBQT 配体；不适用于 AD4 maps 或柔性受体。

### S4.1 6 条确定性队列

1. 从 S2 的 ligand PDBQT 复制五份，命名为 `01` 至 `05`，内容完全相同；另准备一份只把一个 AutoDock 原子类型改为无效值的 `06_invalid_atom_type.pdbqt`。
2. 使用 S2 参数，设置 `max_retries=1`、`top_n=5`，创建队列。
3. 完成后检查总条目、成功/失败数、每次尝试目录、输入快照、日志和 CSV。

通过条件：5 成功、1 失败；无效配体共尝试 2 次；五个有效配体的分数相同；相同分数仍按初始稳定顺序；失败项不阻塞后续配体；Summary 有 6 条数据行，Top N 有 5 条且不含失败项。

### S4.2 取消与恢复

1. 建立 20 个有效配体的队列，以 `exhaustiveness=32`、`cpu=1`、固定 seed 运行。
2. 在第二或第三个配体执行时点击“安全取消”，等待当前配体自然完成。
3. 完全退出 DockStart，重新启动并打开原项目，点击“恢复队列”。

通过条件：取消后不启动新配体；已完成条目不重跑；恢复后顺序和 `attempt_count` 保持；已有 attempt 目录不被覆盖；最终 Summary 有 20 条成功记录。

### S4.3 压力测试

使用 50 份有效 PDBQT、`exhaustiveness=8`、`num_modes=3`、`cpu=1`、固定 seed、`top_n=20`。通过条件：50/50 成功、两个 CSV 行数正确、界面仍能滚动/切页/取消、完成后能打开任意 pose。该案例是稳定性证据，须记录总耗时和峰值内存（若可获得）。

## S5：有限柔性侧链（仅 E2；当前为高级回归）

### 参数与步骤

1. 使用原始 `1FPU.pdb`，不要仅从 receptor PDBQT 反推残基。使用与 S2 相同的 1IEP 配体。
2. 在对接工作台选择“有限柔性”，输入 `A:315`，先执行残基检查，再执行 Meeko 受体准备。第一次必须保持默认严格模式。
3. 官方 1FPU 输入在 Meeko 0.7.1 下应报告 27 个无法匹配模板的残基，严格模式必须停止且不发布半成品。核对列表与 `backend/tests/fixtures/scientific/flexible_1fpu/expected_bad_residues.json`。
4. 人工审阅完整清单；只有确认这些残基可以从本次模型中删除后，勾选明确确认并重新准备。DockStart 必须仅在“实际忽略清单”和“已确认清单”完全一致时发布三件套。
5. 检查该准备记录内的 rigid PDBQT、flex PDBQT、receptor JSON、stdout、stderr、输入 SHA256、`--allow_bad_res` 和已确认/实际忽略清单。
6. 设置：Box `15.190, 53.903, 16.917 / 20,20,20`，`vina`，`exhaustiveness=32`，`num_modes=9`，`cpu=1`，`seed=1431646130`，执行 run。
7. 对同一冻结 rigid/flex 文件执行 CLI 对照，确认命令同时使用 `--receptor` 和 `--flex`。
8. 分别输入 `A:999`、`A:315:Z`、水分子、配体对象及含未解决 altloc 的测试文件。

### 通过条件

- rigid、flex、JSON 三件套非空且可读取；主链仍在 rigid 中，flex 不与 rigid 重复；任一生成失败不会覆盖上一份有效三件套；
- 默认严格模式不能自动使用 `--allow_bad_res`；用户确认前项目保持刚性，确认后 metadata 同时记录已确认清单和 Meeko 实际忽略清单；
- 若确认后输入或 Meeko 诊断发生变化，清单不完全一致时必须拒绝发布三件套；
- DockStart 与 CLI affinity 差异不超过 `0.01 kcal/mol`；输出包含配体与柔性残基坐标；
- 无效选择在准备前被阻止，并有链/编号/对象类型的中文原因；
- 参考科学范围：若输入准备一致，最佳 affinity 可参考约 `-10.8` 至 `-12.5 kcal/mol`。这不是单独的工程通过条件。

当前柔性 run 的拓扑 SDF 导出被安全阻断；若界面拒绝这项导出并保留原始 PDBQT，应记录为符合当前边界，而非失败。

## S6：Meeko 大环准备与安全 SDF（仅 E2；当前为高级回归）

### S6.1 自动断环

1. 使用 `backend/tests/fixtures/scientific/macrocycle_bace1`。先核对 `fixture_manifest.json` 中全部 SHA256；`BACE_1_ligand.sdf` 是从同目录官方 MOL2 受控派生的同坐标系输入，不得另行从 PDB 坐标猜键。
2. 在“结构准备”选择“大环自动断环”，记录最小环尺寸、双键惩罚、芳香断环、chorded/equivalent rings 选项。
3. 准备完成后检查 metadata、PDBQT 中的 Meeko REMARK、G*/glue 证据和断环信息。G* 是工具内部伪原子，不应在 Viewer 中作为真实元素解释。
4. 使用官方 Box `center=30.103,6.152,15.584`、`size=20,20,20`，以及 `vina`、`exhaustiveness=64`、`num_modes=20`、`energy_range=5`、`cpu=1`、`seed=12345` 运行。
5. 同一冻结 PDBQT 进行 CLI 对照。之后在结果页导出 SDF，并用 RDKit 或其他可靠工具比较原始配体和每个输出 pose 的重原子数、形式电荷、连接关系与立体化学。

通过条件：Vina 正常完成，DockStart/CLI 分数表一致；`mk_export` 成功；SDF 无 G*，且不改变重原子数、形式电荷或化学连接；每个 pose 能独立导出。若可获得可靠同坐标系参考，科学目标为 top 9 至少一项 RMSD ≤2.5 Å；该目标不替代工程一致性判定。

### S6.2 刚性大环与不安全导出

1. 对相同输入选择“保持大环刚性”，重新准备和运行。
2. 确认无自动断环记录和 G* 伪原子，报告标记为刚性大环。
3. 复制一份输出 PDBQT，移除 `REMARK SMILES`、`REMARK SMILES IDX` 等嵌入拓扑信息后尝试导出 SDF。

通过条件：刚性模式不带断环记录；缺拓扑文件被拒绝导出并说明“不会猜测键级”，同时保留原 PDBQT。

## S7：AutoDock4 maps 协议（E1、E2；需外部 AutoGrid4）

此阶段只测标准非金属、刚性受体、单配体。未配置 `autogrid4.exe` 时，填写“阻塞”；不能把 AutoGrid4 缺失归为 DockStart 随包工具故障。

### 参数与步骤

1. 使用 Scripps AutoDock 4.2.6 官方 `1dwd` 输入，配置外部 `autogrid4.exe` 4.2.6；确认 DockStart 检测到版本和路径。
2. 导入 `1dwd_rec.pdbqt` 和 `1dwd_lig.pdbqt`，切换“AutoDock4（maps）”。
3. 设 Box center `32.192, 14.174, 25.076`，grid points `60,60,60`，spacing `0.375 Å`；生成 maps。
4. 检查 GPF、GLG、`.maps.fld`、各配体原子类型 `.map`、`.e.map`、`.d.map`、manifest 和 SHA256。
5. 使用 `seed=12345`、`exhaustiveness=2`、`cpu=1` 运行；检查 Vina 命令同时含 `--maps` 和 `--scoring ad4`。
6. 检查 `results/ad4_scores.csv` 与 `reports/ad4_docking_report.md`，确认没有覆盖标准 `scores.csv` 或 `docking_report.md`。
7. 修改受体或 Box，尝试再次运行，检查旧 maps 被标为失效并阻断运行。

### 通过条件

- maps 完整且 manifest 可读取；AD4 run 正常完成；
- 固定输入和参数下，本仓库的回归参考最佳评分为 `-11.55 kcal/mol`；允许因外部 AutoGrid4、输入或平台差异出现小幅偏差，但必须记录实际差异并优先完成 CLI 一致性对照；
- AD4 maps 分数不会与 Vina/Vinardo 混表或被描述为可直接比较；
- 受体、Box、maps 或配体原子类型变化后，旧 maps 不可继续运行。

## S8：重启、路径、归档与卸载

### 步骤

1. 在 S1、S4、S5（如适用）和 S7（如适用）的项目中，分别完成一次运行后退出 DockStart、重启 Windows、重新打开项目。
2. 检查历史结果、报告、运行目录、批量队列状态和文件路径。对于 S4，在取消后再执行此步骤并恢复。
3. 对完成的批量任务执行归档；确认可以创建新筛选任务且旧证据未被删除。
4. 在干净安装环境卸载 Basic/Assisted，检查程序目录、用户项目和卸载注册项是否符合安装器提示。用户项目不应在未明确确认时被删除。

### 通过条件

- 已完成 run 的状态、分数、报告和输入快照保持可读；
- 未完成批量任务只能显式恢复，不会静默重跑或覆盖成功项；
- 中文、空格、长路径下的结果与普通路径一致；
- 卸载不会误删用户创建的项目目录。

## 5. 缺陷记录模板

每个失败或阻塞项建立一个 Markdown 文件：

```markdown
# <案例 ID> - <简短标题>

- 环境：E1 / E2 / E3；Windows 版本；DockStart 版本；安装包 SHA256
- 日期与执行人：
- 输入：来源、文件名、SHA256、准备工具与参数
- 操作步骤：最小可复现步骤
- 预期结果：引用本手册对应阶段
- 实际结果：状态、截图、错误代码、stdout/stderr 摘要
- 保留证据：项目目录、run/preparation/screening 记录路径
- 初步分类：安装 / 工具解析 / 准备 / Vina 参数 / 结果解析 / UI / 路径 / 科学输入
```

失败后不要点击“重新生成”覆盖原目录。复制项目或使用新的 run/preparation 编号重新测试。

## 6. 最终阶段报告

测试结束时，在 `evidence\summary.md` 中分别写出：

| 环境 | S0 | S1 | S2 | S3 | S4 | S5 | S6 | S7 | S8 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E1 Basic |  |  |  |  |  | 不适用 | 不适用 |  |  |  |
| E2 Assisted |  |  |  |  |  |  |  |  |  |  |
| E3 源码 |  |  |  |  |  |  |  |  |  | 仅定位用途 |

发布层面的最低结论应分开写：

1. **Basic 可发布**：E1 的 S0、S1、S2、S4、S8 通过；若宣传 AD4 maps，再要求 S7 通过或明确 AutoGrid4 为用户自备前置条件。
2. **Assisted 可发布**：E2 的 S0、S1、S2、S3、S8 通过，且干净安装、离线 raw→PDBQT→Vina→报告和卸载均有证据。S5/S6 若对外宣传为可用能力，也必须通过对应真实案例。
3. **二级能力已真实验收**：除了源码测试外，S4 的 50 配体、S5 的 1FPU 和 S6 的官方 BACE_1 都有真实工具链证据；任何一个未测只能写“源码主链完成/待真实回归”。
