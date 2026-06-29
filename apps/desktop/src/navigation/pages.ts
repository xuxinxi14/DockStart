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
  | "viewer"
  | "report"
  | "help";

export type NavigationItem = {
  id: PageId;
  group: "Project" | "Workflow" | "Workbench" | "Support";
  label: string;
  description: string;
  requiresProject?: boolean;
  disabled?: boolean;
};

export const appVersion = "0.9.1";

export const navigationItems: NavigationItem[] = [
  {
    id: "home",
    group: "Project",
    label: "总览",
    description: "项目状态、下一步和主要入口",
  },
  {
    id: "project-create",
    group: "Project",
    label: "创建 / 打开项目",
    description: "建立或加载项目工作目录",
  },
  {
    id: "toolchain-status",
    group: "Support",
    label: "工具链",
    description: "Vina、Python、RDKit、Meeko",
  },
  {
    id: "structure-fetch",
    group: "Workflow",
    label: "获取结构",
    description: "受体与配体 raw 文件",
    requiresProject: true,
  },
  {
    id: "preparation",
    group: "Workflow",
    label: "准备 Vina 输入",
    description: "生成或导入 PDBQT",
    requiresProject: true,
  },
  {
    id: "box-setup",
    group: "Workflow",
    label: "设置搜索范围",
    description: "Box 中心与尺寸",
    requiresProject: true,
  },
  {
    id: "vina-config",
    group: "Workflow",
    label: "运行对接",
    description: "配置、运行、解析",
    requiresProject: true,
  },
  {
    id: "result",
    group: "Workflow",
    label: "结果与报告",
    description: "scores 与实验记录",
    requiresProject: true,
  },
  {
    id: "viewer",
    group: "Workbench",
    label: "3D 查看 / Box",
    description: "结构、Box、构象",
    requiresProject: true,
  },
  {
    id: "report",
    group: "Workbench",
    label: "实验记录",
    description: "导出 Markdown 实验记录",
    requiresProject: true,
  },
  {
    id: "help",
    group: "Support",
    label: "文档帮助",
    description: "流程、文件、边界",
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
  viewer: "3D 分子工作台",
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
