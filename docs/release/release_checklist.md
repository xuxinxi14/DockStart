# Release Checklist

## v0.14.1 AD4 证据绑定协议

- 三条门禁新生成的成功和失败 JSON 均为 `schema_version=2`。完成科学 workflow 后生成的 schema v2（无论科学 oracle 成功或失败）必须具有完整的 `source_bound` provenance；导入前检查或绑定阶段失败时必须改为结构化 `ok=false` 与 `unbound` provenance，这类结果不能进入证据包。schema v1 只作为历史兼容格式，必须完全没有 provenance，并标记为 `legacy_unbound`；schema v1 携带任何 provenance 都失败。`--require-source-bound` 只接受三份均为 schema v2 且 `source_bound` 的证据。
- provenance 的 `evidence_payload` 是对“除顶层 `provenance` 外的完整证据对象”进行确定性 canonical JSON 编码后的大小与 SHA256。`tools`、`maps`、`run.command`、串行 attempts、输出身份或任意其他 payload 字段在绑定后被改写，都会在科学 oracle 前失败。
- 每条门禁先在尚未导入 `adapters`/`dockstart_core` 时捕获 A（pre-import）源码快照；随后延迟导入后端并立即捕获 B（imports-verified）模块来源，只有 A/B source fingerprint 相同才允许进入科学 workflow；结束时再捕获 C。A/B/C UTC 必须单调不降且 source fingerprint 相同，B/C import-origin 清单也必须相同。workflow 期间不得延迟导入新的 `adapters`/`dockstart_core` 模块，否则证据失败关闭。该链仍不能证明内存中的 Python 字节码未经进程内恶意替换。
- 已加载的 `adapters` 与 `dockstart_core` 模块必须来自仓库内与模块名精确对应的 `.py` 或 `__init__.py`；模块来源清单自身也具有 canonical SHA256。任一 provenance 构建失败都必须输出顶层结构化错误、`ok=false` 和 `binding_status=unbound`，不得从异常处理路径逃逸或伪装成 source-bound。
- 汇总器对每份输入只读取一次字节，并使用同一份字节计算大小、SHA256 和解析 JSON。bundle 的 `--output` 不得与 evidence 输入碰撞；三条 gate 的 `--output` 也不得与 receptor、ligand、Python、Vina、AutoGrid4 或仓库 fixture/manifest 输入碰撞。碰撞必须在后端导入和科学 workflow 前拒绝，只向 stdout 输出错误，不能覆盖输入。文件成功写入使用同目录临时文件加 `os.replace`；父目录 fsync 尚未实现。
- 静态 oracle 同时核对冻结的 Vina 1.2.7 身份、调用者冻结的 AutoGrid4 身份及其 maps 内部连接、精确 map 文件名集合、Vina 参数顺序/语义、输入/输出快照身份。串行门禁还要求 12 项 source/marker/order、attempt ID/命令/输入/config/output/exit code、11 成功与第 7 项 exit 97 失败，以及按 `(affinity, order)` 从成功 attempts 精确导出的 ranking。

历史三份实跑证据保持原字节不变：

- `ad4-flexible-1fpu-attempt3.json`：`9dd3a3e810c365b9d68e3575e51379fa4e7b3ce8a101a434dada9e0a11ce739a`
- `ad4-multiple-ligands-5x72-attempt2.json`：`4cbc16810e4a65b154b684be5c2bee60b2ca54526a978ceb5f24aa876726b9f0`
- `ad4-serial-screening-attempt3.json`：`af8baeb84ae34b414bc643897c0bd8d72558dc42af9023b59e1ad4290c0a8d75`

它们都是 schema v1：默认汇总可以在三项静态 oracle 通过时返回 `legacy_unbound`，严格模式必须返回 `AD4_EVIDENCE_SOURCE_BINDING_REQUIRED`。禁止事后补写 provenance。

安全边界：这些哈希提供的是意外改写和普通篡改的完整性检测，不是对有能力同时编辑 JSON、修改/执行当前 Python 代码的攻击者的真实性证明。本协议没有数字签名、可信时间戳、只追加外部日志或独立信任根。后续如需要更强保证，应单独设计主动签名/外部可信日志；本机绝对路径匿名化和父目录 fsync 也只列为未来改进，不回写历史证据。

Meeko 工具记录当前固定的是解释器身份、检测状态和包版本；它不等价于对整个 `site-packages/meeko` 目录逐文件哈希。该限制属于现有工具证据边界，不能表述为完整的 Meeko package-bytes 证明。schema v1 + provenance 的禁令只适用于三份 gate 输入；bundle 自身的 schema v1 summary provenance 是不同的汇总格式。

只读核对三门禁证据，不会重新运行 AutoGrid4/Vina：

```powershell
python scripts/verify_ad4_v0141_evidence_bundle.py `
  output/qa/v0.14.1/ad4-flexible-1fpu-attempt3.json `
  output/qa/v0.14.1/ad4-multiple-ligands-5x72-attempt2.json `
  output/qa/v0.14.1/ad4-serial-screening-attempt3.json `
  --output <isolated-bundle-summary.json>
```

默认模式会在三门科学 oracle 均通过时保留历史结论，但汇总必须写明 `binding_status=legacy_unbound`。正式要求当前源码绑定时追加 `--require-source-bound`；上述旧证据必须非零退出。只有在当前源码上重新执行三条真实外部门禁后生成的新证据，才可能满足该选项。

DockStart v1.0.2 是非最终的本地候选版本。Basic 与 Assisted 是两个隔离的发布 profile，
不是产品成熟度标签；本清单不会把任一 profile 称为正式 Stable Release。三条外部 AD4
科学门禁已在固定本地工具链上实跑通过，但本轮不重新打包，也不因此升级为正式 Release。

## Git、版本与候选身份

- 正常候选构建只允许在 `main` 分支和干净工作树执行；
- `-AllowDirtyDevelopmentBuild` 只用于显式的本地开发产物，manifest 必须记录
  `worktree_dirty=true` 和 `development_override`，且不得发布；
- 后端 `__init__.py`、`package.json`、`package-lock.json`、`Cargo.toml`、`Cargo.lock`、
  `tauri.conf.json`、`pages.ts` 七处权威版本必须全部为 `1.0.2`；
- `candidate_id` 必须同时包含版本、源码短 commit 和 UTC 构建时间；替代旧候选时显式记录
  `supersedes_candidate`，不得仅凭同名文件覆盖；
- `artifact-manifest.json` 必须记录完整源码 commit、分支、构建时间、profile、工作树状态、
  `maturity=local_candidate`、三条外部科学验收状态，以及每个 artifact 的相对路径、大小和 SHA256；
- v1.0.2 的所有构建结果均保持 `candidate=true`、`publishable=false`。通过候选门禁只允许写为
  `candidate_gates_passed`，不能自动升级为正式 Release；
- 本地候选验收不冒充 GitHub Release。只有后续明确发布时才创建并推送 tag；
- 安装包、`.release/`、`dist/`、`target/`、runtime 二进制和真实 docking 输出不提交 Git。

## 本轮最小源码检查

v1.0.2 打包必须运行 `scripts/check_all.ps1` 定义的完整自动检查；完整回归仍不能替代独立外部科学门禁：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_all.ps1 `
  -RequireReleaseResources
```

- 统一入口执行 Python compileall/全量 unittest、全部前端异步测试、TypeScript/Vite 生产构建、
  `cargo fmt --check`、`cargo check/test/clippy --locked`；
- 前端测试入口必须递归、确定性地发现全部 `tests/**/*.test.ts`，新增测试不依赖手工维护列表；
- Tooltip 仅在内容可用且控件未禁用时建立 `aria-describedby`，并保留控件原有描述 ID；
- 两个 PowerShell 构建脚本至少通过 parser 语法检查；
- 普通干净克隆缺少未提交 runtime 时只跳过 release-resource 集成测试；构建脚本使用
  `-RequireReleaseResources` 将其升级为失败关闭；
- 无新增 Python、Rust、npm 运行时依赖，无项目 schema 或用户数据迁移。

## 三条独立 AD4 外部验收门禁

这三条都是维护者显式执行的离线外部门禁。验收器只提交 metadata-only 来源契约，不下载、
不内置 AutoGrid4，也不把调用者提供的 1IEP/5X72 上游输入复制进发布包。运行前必须提供契约中
精确 SHA256 的官方输入、AutoDock Vina 1.2.7，以及调用者固定 SHA256 的 AutoGrid4。

### 1FPU 有限柔性 AD4

```powershell
python scripts/verify_ad4_flexible_1fpu.py `
  --ligand-pdbqt <official-1iep-ligand.pdbqt> `
  --autogrid4 <autogrid4.exe> `
  --autogrid4-sha256 <sha256> `
  --output <isolated-result-file.json>
```

- 严格模式必须先产生固定的不完整残基审查清单，确认后 rigid/flex/JSON 输出哈希完全匹配；
- GPF、GLG、刚性受体、完整 map 集、AutoGrid4/Vina 身份和命令必须冻结并逐项复核；
- Vina 命令恰好包含 `--flex`、`--maps` 和 `--scoring ad4`，不得回退到 `--receptor`；
- 输出必须保留 Thr315 柔性标记，结合能落在固定容差内，重原子姿势检查通过。

### 5X72 双配体联合 AD4

```powershell
python scripts/verify_ad4_multiple_ligands_5x72.py `
  --receptor-pdbqt <official-5x72-receptor.pdbqt> `
  --ligand-p59-pdbqt <official-p59.pdbqt> `
  --ligand-p69-pdbqt <official-p69.pdbqt> `
  --autogrid4 <autogrid4.exe> `
  --autogrid4-sha256 <sha256> `
  --output <isolated-result-file.json>
```

- maps 原子类型必须覆盖 P59/P69 的精确并集；成员顺序固定为 P59、P69；
- 命令只能有一个 `--ligand`，其后依次传入两个冻结快照，并包含冻结 maps 与 `ad4` 评分；
- 结果只允许联合 mode 和联合 affinity，不得伪造或导出每个成员的独立分数；
- 输出成员顺序、报告边界声明及全部输入/工具/输出证据必须一致。

### 固定 12 项串行 AD4 队列

```powershell
python scripts/verify_ad4_serial_screening.py `
  --receptor-pdbqt <official-5x72-receptor.pdbqt> `
  --ligand-p59-pdbqt <official-p59.pdbqt> `
  --ligand-p69-pdbqt <official-p69.pdbqt> `
  --autogrid4 <autogrid4.exe> `
  --autogrid4-sha256 <sha256> `
  --output <isolated-result-file.json>
```

- 12 项只用于验证队列可靠性，不作为 12 个独立化学实体的科学基准；每项必须是一次独立 Vina run；
- 一组冻结 maps 被全部 attempt 复用；篡改 map 后恢复必须失败关闭，恢复原字节后才能继续；
- 第 3 项运行中取消并恢复，第 7 项固定失败，队列仍须完成为 11 成功/1 失败且不重跑已完成项；
- 每个配体恰好一条 attempt 证据；Top-N 排序、报告、归档与 ZIP 中 12 份 attempt 记录一致。

### 本轮实跑记录（2026-08-05）

- 固定工具身份：Vina 1.2.7 SHA256 `e0c4b2715e0c1a74f6e92d0f3be0328ac97542eafbc111e6b1efad897a73cce5`；AutoGrid 4.2.6 SHA256 `797efce687d1ae82df59726461e0e1966b3d8edb0f8b187f982fa1ab0c12da9e`；
- 1FPU 柔性 AD4：最佳评分 `-14.2 kcal/mol`，首构象无拟合重原子 RMSD `1.086823 Å`；通过文件 `ad4-flexible-1fpu-attempt3.json`，SHA256 `9dd3a3e810c365b9d68e3575e51379fa4e7b3ce8a101a434dada9e0a11ce739a`；
- 5X72 P59→P69 联合 AD4：9 个联合构象，最佳联合评分 `-17.65 kcal/mol`；通过文件 `ad4-multiple-ligands-5x72-attempt2.json`，SHA256 `4cbc16810e4a65b154b684be5c2bee60b2ca54526a978ceb5f24aa876726b9f0`；
- 固定 12 项串行 AD4：12 次独立 attempt、11 成功/第 7 项固定失败、取消/篡改阻断/恢复/继续/报告/归档/126 项 ZIP 全部符合合同；通过文件 `ad4-serial-screening-attempt3.json`，SHA256 `af8baeb84ae34b414bc643897c0bd8d72558dc42af9023b59e1ad4290c0a8d75`；
- 三份通过证据位于本地忽略目录 `output/qa/v0.14.1/`，不进入 Git 或候选安装包；中间失败 JSON 保留用于审计，最终状态只以本段列出的文件名与哈希为准。`completed_with_failures` 是串行故障注入门禁的预期终态，不代表门禁失败。

任一门禁未执行、失败或来源/哈希不匹配，都必须记录为“未通过”，不能用 mock、单元测试或旧输出替代。

## 候选构建 profile

本轮不执行以下命令。后续需要候选安装包时，从干净 `main` 工作树运行：

```powershell
# 已有 PDBQT 的 Basic profile
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -Profile Basic

# 带离线 RDKit/Meeko 的 Assisted profile
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -Profile Assisted
```

- `.release/basic/` 与 `.release/assisted/` 从白名单 stage 生成，候选目录按版本和 `candidate_id` 隔离；
- Basic 包含 Vina 与精简后端 Python，不含 `site-packages`、RDKit、Meeko 或 preparation CLI；
- Assisted wheelhouse 中每个 artifact 必须匹配 `resources/assisted/SOURCE_MANIFEST.json`，构建不联网；
- AutoGrid4、`AD4Zn.dat` 和外部验收输入不进入任一 stage；
- Assisted 的 development、post-package、post-install 结果全部写入同一 candidate manifest；
- `-SkipPostInstallGate` 产物只能是 `candidate_incomplete`；即使 post-install 通过，v1.0.2 仍为
  `candidate_gates_passed` 和 `publishable=false`。

## 安装与 GUI 验收（后续打包时）

- NSIS 安装到隔离目录，从安装目录运行目标 profile 的最小闭环后静默卸载；
- 安装目录、bundled Python 和卸载注册记录无残留；
- 启动桌面端完成“打开示例 → 准备/导入 → Box/Vina 参数 → 运行 → 结果与报告 → 重启重开”；
- 页面切换不重复启动 Python 或加载同一 3D 模型，后台任务可重连，异常退出的 run 可恢复状态；
- 窄窗口的标准流程卡片底边对齐；对接页底部操作栏处于文档流，不覆盖历史记录或科学边界；
- 浅色/暗色、默认尺寸、可复制文本、链接和右键行为按控件语义正常。

## 发布文案边界

- 明确 Basic/Assisted 是 profile，v1.0.2 的成熟度是非最终本地候选；
- 自动准备仍需人工检查；Docking score 仅供结构结合趋势参考，不能替代实验验证；
- 串行批量是固定受体和冻结 maps 下的多个独立 run，不是联合搜索，也不代表大型数据库虚拟筛选；
- 明确不含 PLIP/ProLIF、Open Babel/MGLTools、相互作用分析、pocket prediction、分子动力学、
  云端或大型数据库虚拟筛选、scoring function 修改；
- 许可证分析属于工程记录，不构成正式法律意见。
