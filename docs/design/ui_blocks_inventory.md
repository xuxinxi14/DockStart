# DockStart UI Blocks Inventory

本文档盘点 DockStart 中所有可设计 UI 板块，是 Molecular Workbench 后续 UI 改造的总清单。

## 字段说明

每个板块都需要记录：

- 用途：这个 UI 解决什么问题。
- 出现页面：应出现在哪些页面或模板。
- 关键字段：主要显示的数据。
- 主操作：用户最重要的操作。
- 空状态：没有数据时如何显示。
- 错误状态：失败或缺失时如何恢复。
- 技术详情：是否默认折叠路径、日志、metadata、manifest、sha256。
- 科学免责声明：是否需要提醒 score、preparation、viewer 边界。
- 实现状态：当前是否已实现。
- 重构判断：是否需要继续重构。

为了让清单可维护，以下表格使用简写：

- 技术详情：是 / 否 / 可选。
- 科学免责声明：是 / 否 / 相关页。
- 实现状态：已实现 / 部分 / 未实现。
- 重构判断：保留 / 调整 / 重构。

## 一、应用外壳

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AppShell | 承载全局布局 | 全部页面 | project、page、toolchain | 导航当前任务 | 无项目提示 | 渲染失败提示 | 否 | 否 | 已实现 | 调整 |
| Sidebar | 工作流导航 | 全部页面 | 分组、状态、禁用原因 | 进入任务 | 无项目禁用工作流 | 项目缺失说明 | 否 | 否 | 已实现 | 调整 |
| Topbar | 项目与阶段摘要 | 全部页面 | 项目名、阶段、工具链、版本 | 打开相关状态页 | 未加载项目 | 工具链异常提示 | 可选 | 否 | 已实现 | 调整 |
| ProjectHeader | 项目总览头部 | Dashboard | 项目名、阶段、下一步 | 继续下一步 | 创建项目入口 | 缺失项提示 | 否 | 相关页 | 部分 | 调整 |
| VersionBadge | 显示版本 | Topbar、About | version | 无 | 版本未知 | 版本不一致 | 可选 | 否 | 已实现 | 保留 |
| ToolchainSummary | 工具链简况 | Topbar、Dashboard | Vina/Python 状态 | 配置工具链 | 未检测 | 检测失败 | 是 | 否 | 部分 | 调整 |
| CurrentProjectBar | 当前项目路径/名称 | Topbar、Dashboard | project_name、project_dir | 打开项目位置 | 未加载项目 | 路径不可读 | 是 | 否 | 部分 | 调整 |
| ContextPanel | 下一步和上下文 | 任务页 | next_action、files、tools | 继续/修复 | 无状态 | 阻塞原因 | 是 | 相关页 | 部分 | 重构 |
| RightRail | 右侧辅助栏 | 任务页、Viewer | status、help、details | 查看详情 | 无辅助信息 | 辅助数据失败 | 是 | 相关页 | 部分 | 重构 |
| MainCanvas | 页面主任务区 | 全部页面 | page state | 执行主任务 | EmptyState | ErrorRecoveryPanel | 可选 | 相关页 | 已实现 | 调整 |

## 二、导航与工作流

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SidebarItem | 单个导航任务 | Sidebar | label、description、status | 切换页面 | disabled reason | 无法进入说明 | 否 | 否 | 已实现 | 调整 |
| SidebarGroup | 导航分组 | Sidebar | Project/Workflow/Workbench/Support | 展开任务 | 无项目禁用 | 分组异常 | 否 | 否 | 已实现 | 保留 |
| WorkflowStepper | 横向步骤提示 | Dashboard、任务页 | steps、current、status | 进入下一步 | 未开始步骤 | 阻塞步骤 | 否 | 相关页 | 已实现 | 调整 |
| WorkflowRail | 纵向流程轨 | Dashboard、ContextPanel | workflow status | 跳转任务 | 无项目 | 缺失项 | 可选 | 相关页 | 部分 | 重构 |
| WorkflowTimeline | 项目进度时间线 | Dashboard | 项目、raw、prepared、box、run、report | 继续下一步 | 未创建项目 | 阶段阻塞 | 可选 | 相关页 | 部分 | 调整 |
| NextActionCard | 下一步建议 | Dashboard、ContextPanel | title、reason、action | 继续下一步 | 无建议 | 阻塞恢复 | 可选 | 相关页 | 部分 | 重构 |
| BlockerPanel | 阻塞说明 | Dashboard、任务页 | blocker、reason、suggestion | 去修复 | 无阻塞不显示 | 展示恢复路径 | 是 | 相关页 | 部分 | 重构 |
| Breadcrumb | 层级位置 | 深层设置/帮助 | page、section | 返回上层 | 顶层不显示 | 链接失效 | 否 | 否 | 未实现 | 可选 |
| PageTabs | 页面内分区 | Help、Settings | tab、count、status | 切换分区 | 无内容 | 加载失败 | 可选 | 相关页 | 部分 | 调整 |
| RunWorkflowBar | Vina 运行链路 | Config/Run/Result/Report | config、run、scores、report | 前往下一环节 | 未准备 | 运行失败 | 是 | 是 | 已实现 | 调整 |

## 三、项目与首页

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| EmptyProjectHero | 无项目首页 | Dashboard | 标题、说明、CTA | 创建项目 | 默认即为空态 | 创建失败 | 否 | 否 | 已实现 | 调整 |
| ProjectDashboard | 项目驾驶舱 | Dashboard | project、workflow、artifacts | 继续下一步 | EmptyProjectHero | workflow 加载失败 | 可选 | 是 | 已实现 | 调整 |
| ProjectSummaryCard | 项目摘要 | Dashboard | 名称、目录、阶段 | 打开项目目录/继续 | 未加载项目 | 目录不可读 | 是 | 否 | 部分 | 调整 |
| ReadinessPanel | 准备度检查 | Dashboard | missing、blocked、ready | 修复缺失项 | 无项目 | 状态接口失败 | 是 | 是 | 已实现 | 调整 |
| ArtifactList | 产物列表 | Dashboard、Report | raw、prepared、config、run、scores、report | 打开/查看 | 未生成 | 文件缺失 | 是 | 相关页 | 已实现 | 调整 |
| RecentRunCard | 最近运行 | Dashboard、Result | run_id、status、best score | 查看结果 | 无运行 | run metadata 失败 | 是 | 是 | 部分 | 调整 |
| ProjectCreateForm | 创建项目 | ProjectCreate | name、path | 创建项目 | 空表单 | 路径错误 | 可选 | 否 | 已实现 | 调整 |
| ProjectOpenEntry | 打开项目 | Dashboard、ProjectCreate | project_dir | 打开已有项目 | 无最近项目 | 路径不可读 | 是 | 否 | 部分 | 重构 |
| FirstRunGuide | 首次流程提示 | Dashboard、Help | 4/6 步流程 | 开始第一步 | 无项目显示 | 无 | 否 | 是 | 部分 | 调整 |
| OnboardingGuide | 新手引导 | Dashboard、Help | step、explanation | 进入步骤 | 无项目版 | 无 | 否 | 是 | 已实现 | 调整 |

## 四、工具链

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ToolchainOverview | 工具链总览 | Toolchain、Dashboard | Vina/Python/RDKit/Meeko | 配置工具链 | 未检测 | 检测入口失败 | 是 | 否 | 已实现 | 调整 |
| VinaStatusCard | Vina 状态 | Toolchain | status、source、version、path | 配置 Vina 路径 | 未检测 | Vina 不可用 | 是 | 否 | 已实现 | 调整 |
| PythonStatusCard | Python 状态 | Toolchain | source、version、path | 配置 Python | 未检测 | Python 不可用 | 是 | 否 | 已实现 | 调整 |
| RDKitStatusCard | RDKit 状态 | Toolchain、Preparation | import、version、capability | 查看 conda 指南 | 未检测 | import 失败 | 是 | 是 | 部分 | 调整 |
| MeekoStatusCard | Meeko 状态 | Toolchain、Preparation | import、version、capability | 查看指南 | 未检测 | preparation API 缺失 | 是 | 是 | 部分 | 调整 |
| BundledResourceCard | 内置资源 | Toolchain | manifest、sha256、exists | 查看技术详情 | 未内置 | manifest 不一致 | 是 | 否 | 已实现 | 调整 |
| ConfiguredPathCard | 用户路径 | Toolchain、Settings | path、exists、source | 修改路径 | 未配置 | 路径不可用 | 是 | 否 | 部分 | 调整 |
| ToolchainErrorPanel | 工具链错误恢复 | Toolchain | title、message、suggestion | 打开设置/帮助 | 无错误不显示 | 展示 raw_error 折叠 | 是 | 否 | 部分 | 重构 |
| ToolchainSetupGuide | 配置向导 | Toolchain、Help | Vina、Python、conda 步骤 | 查看说明 | 未配置显示 | 指南缺失 | 可选 | 否 | 部分 | 重构 |
| TechnicalDetailsDisclosure | 技术详情折叠 | Toolchain、Run、Preparation | stdout、stderr、manifest、sha256 | 展开/复制 | 无详情 | 详情读取失败 | 是 | 否 | 已实现 | 调整 |

## 五、结构获取 raw workflow

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| StructureFetchPage | 获取原始结构 | StructureFetch | receptor raw、ligand raw | 获取结构 | 无项目/无 raw | 下载失败 | 是 | 是 | 已实现 | 调整 |
| ReceptorRawPanel | 受体 raw 区 | StructureFetch | PDB ID、format、raw_file | 下载受体 | 无 raw | RCSB 失败 | 是 | 是 | 已实现 | 调整 |
| LigandRawPanel | 配体 raw 区 | StructureFetch | CID/name、SDF、raw_file | 下载配体 | 无 raw | PubChem 失败 | 是 | 是 | 已实现 | 调整 |
| RawFileStatusCard | raw 状态 | StructureFetch、Dashboard | exists、size、source | 清除记录 | 未下载 | 记录不一致 | 是 | 是 | 已实现 | 调整 |
| RawDownloadForm | raw 下载表单 | StructureFetch | source_id、format、overwrite | 下载 | 空输入 | 输入无效/网络错误 | 是 | 是 | 已实现 | 调整 |
| PubChemQueryForm | PubChem 查询 | StructureFetch | CID/name/query type | 下载配体 | 空输入 | 查询失败 | 是 | 是 | 已实现 | 调整 |
| PdbIdInput | PDB ID 输入 | StructureFetch | pdb_id、format | 获取受体 | 空输入 | 格式无效 | 否 | 是 | 已实现 | 保留 |
| OverwriteToggle | 覆盖开关 | StructureFetch、Preparation | overwrite | 允许覆盖 | 默认关闭 | 覆盖风险提示 | 否 | 相关页 | 已实现 | 保留 |
| ClearRawRecordDialog | 清除 raw 记录 | StructureFetch | target、delete_file | 清除记录 | 无记录禁用 | 删除失败 | 是 | 是 | 部分 | 调整 |
| RawVsPreparedNotice | raw 与 prepared 提示 | StructureFetch、Import、Preparation | 说明文本 | 去准备 PDBQT | 无 | 无 | 否 | 是 | 已实现 | 调整 |

## 六、PDBQT preparation

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PreparationPage | 准备 Vina 输入 | Preparation | receptor/ligand 状态 | 生成 PDBQT | 无 raw | 准备失败 | 是 | 是 | 已实现 | 调整 |
| ReceptorPreparationPanel | 受体准备 | Preparation | raw、prepared、status | 准备受体 | 无 receptor raw | Meeko receptor 失败 | 是 | 是 | 已实现 | 调整 |
| LigandPreparationPanel | 配体准备 | Preparation | raw、prepared、status | 准备配体 | 无 ligand raw | RDKit/Meeko 失败 | 是 | 是 | 已实现 | 调整 |
| PreparationStatusCard | 准备状态 | Preparation、Dashboard | status、output、latest | 查看日志 | 未开始 | failed | 是 | 是 | 已实现 | 调整 |
| PreparationActionPanel | 准备操作 | Preparation | target、overwrite | 生成 prepared PDBQT | 不满足前置 | 执行失败 | 是 | 是 | 部分 | 调整 |
| PreparationLogPanel | 准备日志 | Preparation | stdout、stderr、log | 展开日志 | 无日志 | 读取失败 | 是 | 是 | 已实现 | 调整 |
| PreparationMetadataPanel | 准备 metadata | Preparation | metadata、command、snapshot | 展开技术详情 | 无记录 | JSON 读取失败 | 是 | 是 | 部分 | 调整 |
| ToolCapabilityPanel | 工具能力 | Preparation、Toolchain | Python/RDKit/Meeko | 配置工具链 | 未检测 | 检测失败 | 是 | 是 | 已实现 | 调整 |
| PreparationWarningNotice | 科学检查提示 | Preparation | 检查边界 | 查看帮助 | 无 | 无 | 否 | 是 | 已实现 | 保留 |

## 七、Box 与 Vina 参数

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BoxSetupPanel | 设置搜索范围 | BoxSetup、Viewer | center、size | 保存 Box | 默认参数 | 参数无效 | 可选 | 是 | 已实现 | 调整 |
| BoxParameterGrid | Box 参数网格 | BoxSetup、Viewer | center_x/y/z、size_x/y/z | 编辑参数 | 默认值 | 非数字/过大 | 否 | 是 | 已实现 | 调整 |
| BoxVisualizationSummary | Box 可视化摘要 | BoxSetup、Viewer | box size、volume | 打开 Viewer | 无结构 | viewer 不可用 | 可选 | 是 | 部分 | 调整 |
| VinaParamForm | Vina 参数 | VinaParam | exhaustiveness、modes、cpu、seed | 保存参数 | 默认值 | 参数范围错误 | 可选 | 是 | 已实现 | 调整 |
| VinaParamSummary | 参数摘要 | VinaConfig、RunPrepare | Vina params | 生成配置 | 未设置 | 缺失参数 | 是 | 是 | 部分 | 调整 |
| ParameterValidationMessage | 参数校验 | Box/VinaParam/Config | field、message | 修复输入 | 无错误 | 阻止提交 | 否 | 相关页 | 部分 | 调整 |
| LargeBoxWarning | 大 box 警告 | BoxSetup、Viewer、Config | size、threshold | 确认/修改 | 不触发不显示 | 无 | 否 | 是 | 已实现 | 调整 |
| SearchSpaceNotice | 搜索空间说明 | BoxSetup、Viewer | 说明文本 | 打开帮助 | 无 | 无 | 否 | 是 | 部分 | 调整 |

## 八、3D Viewer / Workbench

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ViewerPage | 3D 查看入口 | Viewer | source、pose、box | 加载结构 | 无文件 | viewer 加载失败 | 是 | 是 | 已实现 | 调整 |
| ViewerWorkbench | 三栏工作台 | Viewer | inspector、canvas、properties | 查看结构 | ViewerEmptyState | 结构无法显示 | 是 | 是 | 已实现 | 调整 |
| StructureInspector | 左侧结构检查 | Viewer | receptor、ligand、pose | 选择来源 | 无结构 | 文件缺失 | 是 | 是 | 部分 | 调整 |
| ViewerCanvas | 3D 画布 | Viewer | molecule data | zoom/reset | 无文件 | 3Dmol 失败 | 可选 | 是 | 已实现 | 调整 |
| ViewerToolbar | 画布工具 | Viewer | zoom、reset、box | 控制视图 | 无结构禁用 | 操作失败 | 否 | 是 | 部分 | 调整 |
| MoleculeLayerToggle | 分子层开关 | Viewer | receptor、ligand、pose | show/hide | 无 layer | layer 缺失 | 否 | 是 | 部分 | 调整 |
| BoxOverlayControl | Box 覆盖控制 | Viewer | show_box、box params | 显示/隐藏 Box | 无 box | box 无效 | 否 | 是 | 已实现 | 调整 |
| PoseSelector | 构象选择 | Viewer | run、mode、score | 切换 pose | 无 out.pdbqt | pose 解析失败 | 是 | 是 | 已实现 | 调整 |
| PoseScorePanel | 对接评分 | Viewer、Result | affinity、RMSD | 查看结果 | 无 scores | scores 缺失 | 是 | 是 | 已实现 | 调整 |
| ViewerFileInfo | 文件信息 | Viewer | file、size、modified | 打开详情 | 无文件 | 文件不可读 | 是 | 是 | 部分 | 调整 |
| ViewerEmptyState | 无文件画布 | Viewer | next step | 去获取/准备 | 默认显示 | 无 | 否 | 是 | 已实现 | 调整 |
| ViewerWarningPanel | Viewer 警告 | Viewer | warning、suggestion | 修复/继续 | 无警告不显示 | 展示恢复建议 | 可选 | 是 | 部分 | 调整 |
| StructurePreviewDrawer | 结构预览抽屉 | Viewer | preview text、metadata | 展开 | 无预览 | 读取失败 | 是 | 是 | 部分 | 调整 |

## 九、Vina 运行

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| VinaConfigPage | 生成运行配置 | VinaConfig | receptor、ligand、box、params | 生成配置 | 缺前置 | 配置失败 | 是 | 是 | 已实现 | 调整 |
| ConfigPreviewPanel | 配置预览 | VinaConfig | vina_config.txt | 展开预览 | 无配置 | 读取失败 | 是 | 是 | 已实现 | 调整 |
| ConfigPrerequisiteStatus | 配置前置检查 | VinaConfig | prepared、box、params | 去修复 | 未满足 | 阻塞 | 可选 | 是 | 部分 | 调整 |
| RunPreparePage | 创建 run 记录 | RunPrepare | run_id、command | 创建运行记录 | 无 config | 创建失败 | 是 | 是 | 已实现 | 调整 |
| CommandPreviewPanel | 命令预览 | RunPrepare | command array | 展开命令 | 无命令 | 生成失败 | 是 | 是 | 已实现 | 调整 |
| RunExecutePage | 执行 Vina | RunExecute | run_id、status | 开始对接 | 无 run | 执行失败 | 是 | 是 | 已实现 | 调整 |
| RunStatusPanel | 运行状态 | RunExecute、Dashboard | status、exit_code、time | 查看日志 | 未运行 | failed | 是 | 是 | 已实现 | 调整 |
| StdoutPanel | stdout | RunExecute | stdout text | 展开/复制 | 无 stdout | 读取失败 | 是 | 否 | 已实现 | 保留 |
| StderrPanel | stderr | RunExecute | stderr text | 展开/复制 | 无 stderr | 读取失败 | 是 | 否 | 已实现 | 保留 |
| LogFilePanel | log.txt | RunExecute、Result | log content | 解析结果 | 无 log | 读取失败 | 是 | 是 | 已实现 | 调整 |
| RunRequiredPage | run 缺失占位 | Result、Report | required step | 去准备/执行 | 默认页面 | 无 | 否 | 是 | 已实现 | 调整 |

## 十、结果与报告

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ResultPage | 查看结果 | Result | scores、run、pose | 解析/查看 scores | 无 run/log | 解析失败 | 是 | 是 | 已实现 | 调整 |
| ScoreSummaryCard | score 摘要 | Result、Dashboard | best_affinity、count | 查看表格 | 无 scores | 数据异常 | 是 | 是 | 部分 | 调整 |
| ScoresTable | scores 表格 | Result | mode、affinity、RMSD | 选择 pose | 无数据 | CSV 读取失败 | 是 | 是 | 已实现 | 调整 |
| BestAffinityCard | 最低对接评分 | Result | affinity | 打开 pose | 无 score | 无 | 可选 | 是 | 部分 | 调整 |
| PoseResultCard | 构象结果 | Result、Viewer | mode、score、path | 查看构象 | 无 pose | pose 缺失 | 是 | 是 | 部分 | 调整 |
| ReportPage | 导出实验记录 | Report | report path、status | 导出 Markdown | 无 run/scores | 导出失败 | 是 | 是 | 已实现 | 调整 |
| ReportStatusCard | 报告状态 | Report、Dashboard | exists、path、modified | 打开/导出 | 未生成 | 文件缺失 | 是 | 是 | 已实现 | 调整 |
| ReportExportPanel | 导出面板 | Report | run、scores、report path | 导出实验记录 | 无结果 | 导出失败 | 是 | 是 | 部分 | 调整 |
| MarkdownReportPath | 报告路径 | Report | path | 复制路径 | 未生成 | 路径不可用 | 是 | 是 | 部分 | 调整 |
| ScientificDisclaimer | 科学边界 | Result、Report、Viewer | disclaimer text | 无 | 固定显示 | 无 | 否 | 是 | 已实现 | 保留 |

## 十一、错误、空状态、反馈

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| EmptyState | 空状态 | 全部页面 | title、message、action | 执行下一步 | 默认显示 | 无 | 否 | 相关页 | 已实现 | 调整 |
| ErrorRecoveryPanel | 错误恢复 | 全部任务页 | title、message、suggestion | 修复/重试 | 无错误不显示 | 展示 raw error | 是 | 相关页 | 部分 | 重构 |
| WarningCallout | 风险提示 | 多页面 | warning、suggestion | 确认/修复 | 无警告不显示 | 无 | 可选 | 相关页 | 已实现 | 调整 |
| SuccessBanner | 成功反馈 | 操作后 | title、next step | 去下一步 | 不显示 | 无 | 否 | 相关页 | 部分 | 调整 |
| LoadingState | 加载状态 | 全部页面 | label | 等待 | 默认 | 超时提示 | 否 | 否 | 部分 | 调整 |
| SkeletonPanel | 骨架屏 | Dashboard、Toolchain | shape | 等待加载 | 默认 | 超时 | 否 | 否 | 未实现 | 可选 |
| InlineValidation | 表单校验 | 表单页 | field、message | 修复输入 | 无错误 | 阻止提交 | 否 | 相关页 | 部分 | 调整 |
| Toast | 短反馈 | 全局 | message、status | 无 | 无 | 展示失败 | 可选 | 否 | 未实现 | 可选 |
| ConfirmDialog | 确认操作 | 清除/覆盖 | action、risk | 确认/取消 | 不显示 | 执行失败 | 可选 | 相关页 | 部分 | 调整 |
| DangerZone | 危险操作 | Settings/清除记录 | delete/reset | 确认 | 默认折叠 | 执行失败 | 是 | 相关页 | 部分 | 可选 |

## 十二、文件、路径、日志

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FileChip | 文件摘要 | 全部任务页 | filename、status | 查看详情 | 无文件 | 文件缺失 | 可选 | 相关页 | 部分 | 重构 |
| FileStatusRow | 文件状态行 | Dashboard、ContextPanel | file、exists、size | 打开详情 | 无文件 | 缺失 | 是 | 相关页 | 部分 | 调整 |
| PathDisplay | 路径显示 | 全部技术详情 | path | 复制 | 无路径 | 路径不可读 | 是 | 否 | 部分 | 重构 |
| CopyPathButton | 复制路径 | PathDisplay | path | 复制 | 无路径禁用 | 复制失败 | 否 | 否 | 部分 | 可选 |
| LogDrawer | 日志抽屉 | Run、Preparation、Toolchain | stdout、stderr、log | 展开/复制 | 无日志 | 读取失败 | 是 | 相关页 | 部分 | 重构 |
| CodeBlock | 代码/配置块 | Config、Run、Help | text、language | 复制 | 无内容 | 读取失败 | 是 | 相关页 | 部分 | 调整 |
| MetadataTable | metadata 表 | Run、Preparation | key/value | 展开 | 无 metadata | JSON 失败 | 是 | 相关页 | 部分 | 调整 |
| ManifestDetails | manifest 详情 | Toolchain | manifest、resource_dir | 展开 | 无 manifest | 校验失败 | 是 | 否 | 部分 | 调整 |
| Sha256Field | sha256 | Toolchain | hash、expected | 复制 | 无 hash | 不匹配 | 是 | 否 | 部分 | 保留 |

## 十三、帮助与文档

| UI 板块 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| HelpPage | 帮助中心 | Help | quick start、FAQ | 查看流程 | 无 | 文档缺失 | 可选 | 是 | 已实现 | 调整 |
| QuickStartGuide | 5 分钟流程 | Help、Dashboard | steps | 开始项目 | 无 | 无 | 否 | 是 | 已实现 | 调整 |
| TerminologyCard | 术语说明 | Help | raw/prepared/pose/score | 查看术语 | 无 | 无 | 否 | 是 | 部分 | 调整 |
| FAQBlock | 常见问题 | Help | question、answer | 展开 | 无 FAQ | 无 | 可选 | 是 | 部分 | 调整 |
| ScienceBoundaryNotice | 科学边界 | Help、Result、Report、Viewer | boundary text | 无 | 固定显示 | 无 | 否 | 是 | 已实现 | 保留 |
| LicenseNotice | 许可证边界 | Help、Toolchain | tool、license、integration | 查看 license notes | 无 | 无 | 可选 | 否 | 部分 | 调整 |
| ExternalToolGuide | 外部工具指南 | Help、Toolchain | Vina/Python/RDKit/Meeko | 打开说明 | 无 | 链接缺失 | 是 | 是 | 部分 | 调整 |

## 十四、页面模板

| 模板 | 用途 | 出现页面 | 关键字段 | 主操作 | 空状态 | 错误状态 | 技术详情 | 科学免责声明 | 实现状态 | 重构判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OverviewPageTemplate | 总览页模板 | Dashboard | project、workflow、readiness | 继续下一步 | EmptyProjectHero | workflow 失败 | 可选 | 是 | 部分 | 调整 |
| StepTaskPageTemplate | 单任务页模板 | Structure/Prep/Config/Run/Report | header、main、context | 唯一主任务 | EmptyState | ErrorRecoveryPanel | 是 | 相关页 | 部分 | 重构 |
| SplitWorkbenchTemplate | 分屏工作台 | Toolchain、Preparation | primary、context | 配置/生成 | 无数据 | 分区错误 | 是 | 相关页 | 部分 | 重构 |
| ViewerWorkbenchTemplate | Viewer 专用模板 | Viewer | inspector、canvas、properties、drawer | 加载/查看结构 | ViewerEmptyState | ViewerWarningPanel | 是 | 是 | 已实现 | 调整 |
| ResultPageTemplate | 结果页模板 | Result | summary、table、pose link | 查看结果 | 无 scores | 解析失败 | 是 | 是 | 部分 | 调整 |
| HelpPageTemplate | 帮助页模板 | Help | quick start、FAQ、boundary | 查看说明 | 无 | 文档缺失 | 可选 | 是 | 部分 | 调整 |
| SettingsPageTemplate | 设置页模板 | Settings、Toolchain | path、tool、validation | 保存配置 | 无配置 | 路径错误 | 是 | 否 | 部分 | 调整 |
