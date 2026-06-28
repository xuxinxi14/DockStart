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

export const appVersion = "0.7.3";

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
    description: "配置 Vina、Python、RDKit 和 Meeko",
  },
  {
    id: "structure-fetch",
    group: "Workflow",
    label: "1 获取结构",
    description: "获取原始结构文件",
    requiresProject: true,
  },
  {
    id: "preparation",
    group: "Workflow",
    label: "2 准备 Vina 输入",
    description: "生成或确认 Vina 输入文件",
    requiresProject: true,
  },
  {
    id: "box-setup",
    group: "Workflow",
    label: "3 设置 Box",
    description: "设置对接搜索范围",
    requiresProject: true,
  },
  {
    id: "vina-config",
    group: "Workflow",
    label: "4 运行 Vina",
    description: "配置、准备并执行对接运行",
    requiresProject: true,
  },
  {
    id: "result",
    group: "Workflow",
    label: "5 查看结果",
    description: "解析 scores 并查看对接构象",
    requiresProject: true,
  },
  {
    id: "viewer",
    group: "Workbench",
    label: "3D 查看",
    description: "结构、搜索范围和对接构象工作台",
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
    description: "查看新手流程、文件说明和科学边界",
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
  viewer: "3D 查看",
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
