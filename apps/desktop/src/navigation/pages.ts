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
  | "run-execute"
  | "result"
  | "report"
  | "help";

export type StartMode = "basic" | "assisted" | "demo";

export type NavigateOptions = {
  startMode?: StartMode;
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

export const appVersion = "0.9.7";

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
    label: "结构准备",
    description: "受体与配体准备",
    requiresProject: true,
  },
  {
    id: "run-prepare",
    group: "Workflow",
    label: "对接工作台",
    description: "搜索范围与运行",
    requiresProject: true,
  },
  {
    id: "result",
    group: "Workflow",
    label: "结果",
    description: "结果分析与实验记录",
    requiresProject: true,
  },
];

export const pageTitles: Record<PageId, string> = {
  home: "项目总览",
  "tool-check": "工具检测",
  "toolchain-status": "配置工具链",
  settings: "工具路径设置",
  "project-create": "创建项目",
  "structure-fetch": "获取原始结构文件",
  preparation: "准备 Vina 输入文件",
  "import-pdbqt": "导入 Vina 输入",
  "box-setup": "设置搜索范围",
  "vina-param": "设置 Vina 参数",
  "vina-config": "生成运行配置",
  "run-prepare": "准备对接运行",
  "run-execute": "执行 AutoDock Vina",
  result: "查看对接结果",
  report: "导出实验记录",
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
 * into the docking console. Keep the legacy ids as compatibility inputs, but
 * never send a normal user flow back to the retired screens.
 */
export function normalizeNavigationPage(page: PageId): PageId {
  if (page === "box-setup" || page === "vina-param" || page === "vina-config") {
    return "run-prepare";
  }
  return page;
}
