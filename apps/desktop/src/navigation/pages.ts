export type PageId =
  | "home"
  | "tool-check"
  | "toolchain-status"
  | "settings"
  | "project-create"
  | "structure-fetch"
  | "preparation"
  | "import-pdbqt"
  | "box-setup"
  | "vina-param"
  | "vina-config"
  | "run-prepare"
  | "hydrated-ad4"
  | "run-execute"
  | "result"
  | "report"
  | "help";

export type StartMode = "basic" | "assisted" | "demo";
export type ProjectTaskIntent = "dock" | "score_only" | "local_only";

export type NavigateOptions = {
  startMode?: StartMode;
  taskIntent?: ProjectTaskIntent;
  runId?: string;
};

export type NavigateHandler = (page: PageId, options?: NavigateOptions) => void;

export type NavigationItem = {
  id: PageId;
  group: "Project" | "Workflow" | "Workbench" | "Support";
  label: string;
  description: string;
  requiresProject?: boolean;
  disabled?: boolean;
};

export const appVersion = "0.12.2";

export const navigationItems: NavigationItem[] = [
  {
    id: "home",
    group: "Project",
    label: "项目",
    description: "项目状态与管理",
  },
  {
    id: "preparation",
    group: "Workflow",
    label: "结构获取与转换",
    description: "在线搜索、导入、转换 PDBQT",
    requiresProject: true,
  },
  {
    id: "run-prepare",
    group: "Workflow",
    label: "运行工作台",
    description: "对接、评分与局部优化",
    requiresProject: true,
  },
  {
    id: "result",
    group: "Workflow",
    label: "结果",
    description: "结果分析与实验记录",
    requiresProject: true,
  },
  {
    id: "hydrated-ad4",
    group: "Workbench",
    label: "水合 AD4",
    description: "实验性显式水对接流程",
    requiresProject: true,
  },
];

export const pageTitles: Record<PageId, string> = {
  home: "项目总览",
  "tool-check": "工具检测",
  "toolchain-status": "配置工具链",
  settings: "工具路径设置",
  "project-create": "创建项目",
  "structure-fetch": "获取或导入原始结构",
  preparation: "格式转换与 PDBQT 准备",
  "import-pdbqt": "导入已有 PDBQT",
  "box-setup": "设置搜索范围",
  "vina-param": "设置 Vina 参数",
  "vina-config": "生成运行配置",
  "run-prepare": "准备运行任务",
  "hydrated-ad4": "实验性水合 AD4",
  "run-execute": "执行 AutoDock Vina",
  result: "查看运行结果",
  report: "结果分析报告",
  help: "文档帮助",
};

export function resolveNavigationTarget(item: NavigationItem, hasProject: boolean): PageId {
  if (item.disabled) {
    return "home";
  }
  if (item.requiresProject && !hasProject) {
    return "project-create";
  }
  return item.id;
}

/**
 * V0.9.5 consolidated the former Box, Vina-parameter and config wizard pages
 * into the run workbench. Keep the legacy ids as compatibility inputs, but
 * never send a normal user flow back to the retired screens.
 */
export function normalizeNavigationPage(page: PageId): PageId {
  if (page === "box-setup" || page === "vina-param" || page === "vina-config") {
    return "run-prepare";
  }
  return page;
}
