import type { PageId } from "../navigation/pages";
import { translate } from "../i18n/translate.ts";

export const DOCUMENTATION_URL = "https://xuxinxi14.github.io/DockStart-Docs/";
export type HelpTopic = {
  id: string;
  title: string;
  group: "入门" | "操作" | "排错" | "进阶";
  summary: string;
  steps: string[];
  skip: string;
  path: string;
  page?: PageId;
  keywords: string[];
};

// These concise, versioned instructions ship with the app and remain available offline.
// Online articles are maintained separately; never send project paths or logs to search.
export const helpTopics: HelpTopic[] = [
  { id: "start", title: "第一次使用：从示例开始", group: "入门", summary: "先熟悉项目、结构准备、运行工作台和结果四个入口。", steps: ["选择内置示例，复制到自己的项目目录。", "已有 PDBQT 可以直接使用；viewer_result 示例可以直接查看已有结果。", "完整操作可参考 1IEP 案例，但软件当前支持的输入格式以文件选择器为准。"], skip: "已有自己的数据和操作经验时，可以跳过示例。示例用于教学，不用于科研结论。", path: "docs/part-b/cases/basic-docking-1iep/", page: "project-create", keywords: ["新手", "快速开始", "demo", "1iep", "示例"] },
  { id: "profiles", title: "Basic 与 Assisted 怎么选", group: "入门", summary: "两种安装档位都能运行已有 PDBQT；Assisted 额外随附结构准备工具。", steps: ["已有受体与配体 PDBQT：Basic 即可。", "需要从受体 PDB/CIF 和配体 SDF/MOL/单分子 MOL2 准备：使用 Assisted 或配置兼容 Python。", "软件按实际检测到的 Vina、Python、RDKit 和 Meeko 判断可用能力。"], skip: "已有 PDBQT 可跳过原始结构转换；缺少 RDKit/Meeko 不会阻止这条路径。", path: "docs/part-c/basic-and-assisted/", page: "toolchain-status", keywords: ["basic", "assisted", "安装包", "rdkit", "meeko"] },
  { id: "structure", title: "结构导入与 PDBQT 准备", group: "操作", summary: "受体和配体分别按实际格式处理，转换后仍需人工检查结构。", steps: ["受体支持 PDBQT/PDB/CIF；配体支持 PDBQT/SDF/MOL/单分子 MOL2，不支持 PDB 或 SMILES 配体自动准备。", "已有 PDBQT 的文件直接使用，需要转换的文件交给兼容的 RDKit/Meeko 工具链。", "检查链、缺失残基、质子化、电荷、手性、水和金属；准备警告需要逐项理解后确认。"], skip: "已有有效 PDBQT 可以跳过转换，但不能跳过对输入结构的科学检查。", path: "docs/part-c/faq-structure-preparation/", page: "preparation", keywords: ["pdbqt", "pdb", "cif", "sdf", "mol2", "格式", "受体", "配体", "原始", "质子化", "电荷", "PREPARATION", "RECEPTOR_FILE", "LIGAND_FILE"] },
  { id: "tasks", title: "全局对接、姿势评分与局部优化", group: "操作", summary: "按是否已有可信结合姿势选择任务，三种结果不能混为一谈。", steps: ["全局对接：在指定 Box 内搜索多个候选构象，“全局”不代表自动扫描整颗蛋白。", "姿势评分：保持输入坐标，只计算当前姿势评分，不产生新构象。", "局部优化：在输入姿势附近微调；评分与局部优化都要求受体/配体处于同一坐标系，并先确认输入姿势。"], skip: "常规首次对接保持全局对接即可；没有可信输入姿势时不要使用评分或局部优化。", path: "docs/part-c/faq-task-types/", page: "run-prepare", keywords: ["全局", "score_only", "local_only", "评分", "局部", "任务", "autobox", "姿势", "attestation", "POSE_INPUT"] },
  { id: "box", title: "搜索范围与 Box 调整", group: "操作", summary: "Box 指定搜索区域，中心和尺寸的单位都是 Å。", steps: ["依据共晶配体、实验或文献确定目标区域，并在 3D 视图中复核。", "“定位到受体”只移动到几何中心，不预测口袋，也不会自动改变尺寸。", "可绑定中心/尺寸字段，用滚轮精调；重置可恢复进入工作台时的参数。"], skip: "常规全局对接必须确认搜索范围；评分/局部优化是否使用自动范围由对应协议决定。", path: "docs/part-a/search-space-and-scoring/search-box/", page: "run-prepare", keywords: ["box", "中心", "尺寸", "搜索范围", "定位", "滚轮", "angstrom", "BOX_"] },
  { id: "maps", title: "评分协议与预计算 maps", group: "操作", summary: "普通 Vina/Vinardo 可实时计算；AutoDock4 必须使用有效 maps。", steps: ["首次使用保持 Vina/Vinardo 和实时计算，不需要保存或导入 maps。", "保存的 Vina/Vinardo maps 有 grid-only/no-refine 等限制，不能与其他协议混用。", "AutoDock4 需要用户安装 AutoGrid4；受体或 Box 改变后，重新生成或导入与当前请求匹配的 maps。"], skip: "普通 Vina/Vinardo 单次实时对接可跳过 maps；AutoDock4 不能跳过有效 maps。", path: "docs/part-c/faq-box-and-maps/", page: "run-prepare", keywords: ["maps", "网格", "评分协议", "vinardo", "ad4", "manifest", "MAPS_", "GRID_"] },
  { id: "parameters", title: "Vina 参数与可复现记录", group: "操作", summary: "搜索强度、输出数量和随机种子共同影响运行与结果。", steps: ["exhaustiveness 控制搜索彻底程度，较高通常更慢。", "num_modes 是最多输出构象数量，energy_range 限制候选能量范围；不保证总能输出指定数量。", "记录 CPU、seed、输入、Box、工具版本和评分协议；相同 seed 本身不能保证不同环境完全一致。"], skip: "首次操作可以保留基础默认值；专家选项可跳过，但必须核实研究目标和搜索范围。", path: "docs/appendix/vina-parameters-quick-reference/", page: "run-prepare", keywords: ["参数", "exhaustiveness", "num_modes", "energy_range", "cpu", "seed", "spacing", "verbosity", "max_evals", "min_rmsd", "no_refine", "force_even_voxels"] },
  { id: "results", title: "构象、结合能与 RMSD", group: "操作", summary: "检查姿势与记录，再解释评分；排序不能直接证明真实结合。", steps: ["选择对应运行，查看构象、结合能和结果分析报告。", "Vina 表格中的 RMSD lb/ub 是相对最佳 Mode 的几何差异，不是与共晶结构比较的验证 RMSD。", "不同评分函数的数值不能直接比较；局部优化位移也不能作为共晶验证。"], skip: "研究结果解释不能跳过结构检查。Docking score 仅供结构结合趋势参考，不能替代实验验证。", path: "docs/part-c/interpreting-results/", page: "result", keywords: ["rmsd", "affinity", "结果", "结合能", "mode", "报告", "pose", "csv"] },
  { id: "toolchain", title: "工具不可用或安装自检失败", group: "排错", summary: "从实际检测状态定位问题，已有 PDBQT 只需 Vina 对接能力。", steps: ["进入工具链页重新检测，检查版本、来源和路径。", "原始结构转换检查 Python、RDKit、Meeko；后端启动失败时检查完整应用目录和 Python runtime。", "仍失败时导出诊断报告，保留中文错误码和操作步骤，脱敏后再反馈。"], skip: "工具检查异常时先处理所需工具；已有 PDBQT 不必安装结构转换依赖。", path: "docs/part-c/toolchain/", page: "toolchain-status", keywords: ["工具", "python", "vina", "检测", "安装", "诊断", "PYTHON_BACKEND_ERROR", "VINA_NOT_FOUND", "PYTHON_NOT_FOUND", "TOOL_"] },
  { id: "autogrid", title: "配置外部 AutoGrid4", group: "排错", summary: "AutoGrid4 是外部可选工具，Basic/Assisted 安装包均不内置。", steps: ["自行安装 AutoGrid4，并在工具链页配置实际可执行文件路径。", "重新检测版本与可读性；按当前受体、Box 和配体原子类型生成网格。", "生成失败时检查 GPF、AutoGrid 日志及参数文件，不手改已归档运行证据。"], skip: "使用普通 Vina/Vinardo 实时对接可以跳过 AutoGrid4；AutoDock4 工作流需要它。", path: "docs/part-c/autogrid4-setup/", page: "toolchain-status", keywords: ["autogrid", "AUTOGRID_", "gpf", "gpl"] },
  { id: "errors", title: "无法开始、运行失败或结果缺失", group: "排错", summary: "先保留项目和日志，按阻塞项与错误码处理。", steps: ["运行前处理所有阻塞项，再重新检查；绿色只表示运行条件满足。", "失败后检查对应 run 的 stderr、log 和 metadata；修复输入或环境后创建新运行。", "无评分时确认对应任务类型与运行状态：对接读取 scores.csv，评分/局部优化读取 evaluation.json。"], skip: "失败诊断不能用删除项目替代。保留原始运行记录可以追溯问题。", path: "docs/part-c/common-errors-and-recovery/", page: "run-prepare", keywords: ["错误", "失败", "阻塞", "中断", "没有评分", "VINA_RUN_FAILED", "RESULT_", "SCORE_", "RUN_", "CONFIG_"] },
  { id: "differences", title: "为什么我的结果与案例不同", group: "排错", summary: "先比较输入结构和协议，再排查参数与环境。", steps: ["检查受体/配体来源、质子化、电荷、Box 和评分函数。", "比较工具版本、随机种子和搜索参数；不要只对照最佳分数。", "数值接近不等于科学验证，仍需评估构象、实验和研究条件。"], skip: "引用案例数值时不能跳过条件核对。", path: "docs/part-c/why-results-differ/", page: "result", keywords: ["不同", "差异", "偏差", "复现", "案例"] },
  { id: "data", title: "安装更新、项目保存与分享", group: "入门", summary: "项目文件与软件目录分开存放，分享前检查路径和研究数据。", steps: ["把项目保存在独立、可写且空间充足的目录，更新软件前备份重要项目。", "Basic 与 Assisted 共用应用身份，切换档位时按安装说明操作。", "报告和诊断可能含本机路径；历史筛选 ZIP 是只读实验记录，不是可恢复运行的项目备份。"], skip: "可跳过在线教程，但不能省略重要项目的备份和分享前检查。", path: "docs/part-c/install-update-data-safety/", page: "home", keywords: ["更新", "卸载", "保存", "备份", "分享", "zip", "路径", "磁盘"] },
  { id: "flexible", title: "有限柔性受体", group: "进阶", summary: "只让选定侧链参与搜索，适用于有结构依据的少量残基。", steps: ["依据目标口袋和结构选择少量柔性残基，不要把所有残基都设为柔性。", "复核刚性受体和柔性侧链文件以及对应协议限制。", "可参考 1FPU 案例；准备模板异常需逐项理解并确认。"], skip: "普通刚性受体对接可以跳过柔性设置。", path: "docs/part-b/cases/flexible-docking-1fpu/", page: "preparation", keywords: ["柔性", "残基", "flexible", "1fpu", "FLEX_"] },
  { id: "batch", title: "串行批量筛选与历史归档", group: "进阶", summary: "多个配体逐个运行，结果和归档保留各自的证据。", steps: ["准备配体库，勾选有效候选，确认冻结的受体、Box 与参数后建队。", "批量筛选使用全局对接；失败项按自身日志排查。", "历史归档只读；两批比较需核实配体身份与协议可比性，满足条件才计算差值。"], skip: "只对接一个配体可以跳过批量、归档比较和导出。", path: "docs/part-b/cases/batch-docking/", page: "run-prepare", keywords: ["批量", "筛选", "队列", "归档", "历史", "batch", "SCREENING_", "ARCHIVE_"] },
  { id: "multiple", title: "两个配体共同对接", group: "进阶", summary: "一个 Mode 是两个配体的一组联合构象，仅有联合评分。", steps: ["使用受支持协议及恰好两个准备好的 PDBQT，复核当前兼容限制。", "它与多个配体逐个运行的串行筛选是不同任务。", "不拆分成员 affinity，不直接比较成员组成不同的联合任务。"], skip: "普通单配体对接可跳过此实验入口。", path: "docs/part-b/cases/multiple-ligands-docking-5x72/", page: "run-prepare", keywords: ["共同", "联合", "多配体", "multiple", "5x72", "MULTIPLE_"] },
  { id: "hydrated", title: "水合 AD4（实验性）", group: "进阶", summary: "显式水协议有独立准备、网格和结果处理条件。", steps: ["先阅读协议边界与 1UW6 案例，再按水合 AD4 专用入口操作。", "需要外部 AutoGrid4 和受支持的准备/参数文件。", "不与普通 maps、柔性或共同对接入口混用。"], skip: "常规单配体对接可以跳过水合流程。", path: "docs/part-b/cases/hydrated-docking-1uw6/", page: "hydrated-ad4", keywords: ["水合", "hydrated", "1uw6", "HYDRATED_"] },
  { id: "zinc", title: "AD4Zn（Beta）", group: "进阶", summary: "Zn 专用协议仅适用于受支持的位点与参数条件。", steps: ["复核单核 Zn 位点和 TZ 生成条件，不泛化到其他金属。", "提供受支持的 AutoGrid4 与用户自行准备的 AD4Zn.dat。", "条件不满足会阻断，不会自动降级为标准 AD4；在线章节尚无经过验证的逐步实测案例。"], skip: "没有符合条件的 Zn 研究目标时可以跳过。", path: "docs/part-b/cases/zinc-ad4zn/", page: "run-prepare", keywords: ["zn", "锌", "tz", "AD4ZN_", "金属"] },
  { id: "macrocycle", title: "大环配体准备", group: "进阶", summary: "先分析大环并确认准备策略，再检查生成的输入。", steps: ["在结构准备页完成分析、确认、准备和继续步骤。", "复核策略、输出结构和警告，保留 preparation 记录。", "参考 BACE1 案例理解操作与协议边界。"], skip: "非大环配体不需要执行大环专用策略。", path: "docs/part-b/cases/macrocycle-docking-bace1/", page: "preparation", keywords: ["大环", "macrocycle", "bace", "MACROCYCLE_"] },
];

export function topicUrl(topic: HelpTopic): string { return new URL(topic.path, DOCUMENTATION_URL).href; }
export function searchDocumentationUrl(query: string): string {
  const url = new URL("search/", DOCUMENTATION_URL);
  if (query.trim()) url.searchParams.set("q", query.trim());
  return url.href;
}
export function searchHelpTopics(query: string): HelpTopic[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return helpTopics.filter(topic => {
    const messages = [topic.title, topic.summary, topic.skip, ...topic.steps, ...topic.keywords];
    const text = messages.flatMap(message => [message, translate(message, undefined, "en-US")]).join(" ").toLowerCase();
    return terms.every(term => text.includes(term));
  });
}
export function helpTopicForError(code: string): HelpTopic {
  const rules: Array<[RegExp, string]> = [
    [/AD4ZN|TZ_/, "zinc"], [/HYDRATED|WATER_/, "hydrated"], [/AUTOGRID/, "autogrid"],
    [/MAPS|GRID|MANIFEST/, "maps"], [/PYTHON|TOOL|VINA_NOT|RDKIT|MEEKO_NOT/, "toolchain"],
    [/FLEX/, "flexible"], [/MACROCYCLE/, "macrocycle"], [/MULTIPLE|JOINT/, "multiple"],
    [/SCREENING|ARCHIVE|BATCH/, "batch"], [/POSE_INPUT|ATTESTATION/, "tasks"],
    [/RECEPTOR|LIGAND|PREPARATION|STRUCTURE/, "structure"], [/BOX/, "box"],
  ];
  const id = rules.find(([pattern]) => pattern.test(code.toUpperCase()))?.[1] ?? "errors";
  return helpTopics.find(topic => topic.id === id)!;
}
export function helpTopicForSubject(subject: string): HelpTopic | undefined {
  const rules: Array<[RegExp, string]> = [
    [/评分协议|maps|网格|scoring protocol|grid/i, "maps"], [/柔性|残基|flexible|residue/i, "flexible"], [/大环|macrocycle/i, "macrocycle"],
    [/搜索彻底|构象数量|能量范围|CPU|随机种子|exhaustiveness|num_modes|energy_range|seed|spacing|verbosity|max_evals|min_rmsd|no_refine|force_even_voxels|number of poses/i, "parameters"],
    [/Box|搜索范围|中心坐标|箱体|尺寸|search space|dimensions|center/i, "box"], [/RMSD|结合能|亲和力|binding energy|affinity/i, "results"],
    [/受体|配体|结构审查|质子化|电荷|receptor|ligand|structure review|protonation|charge/i, "structure"], [/Python|Vina 路径|工具链|toolchain/i, "toolchain"],
    [/运行模式|姿势评分|局部优化|run mode|pose scoring|local optimization/i, "tasks"],
  ];
  const id = rules.find(([pattern]) => pattern.test(subject))?.[1];
  return helpTopics.find(topic => topic.id === id);
}
