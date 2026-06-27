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
  label: string;
  description: string;
  requiresProject?: boolean;
  disabled?: boolean;
};

export const appVersion = "0.6.1";

export const navigationItems: NavigationItem[] = [
  {
    id: "home",
    label: "项目总览",
    description: "查看当前项目、下一步和主要入口",
  },
  {
    id: "toolchain-status",
    label: "工具链",
    description: "检查 Vina、Python、RDKit、Meeko 和内置资源",
  },
  {
    id: "structure-fetch",
    label: "获取结构",
    description: "下载或管理 raw receptor / ligand",
    requiresProject: true,
  },
  {
    id: "preparation",
    label: "准备 PDBQT",
    description: "从 raw 文件生成或确认 prepared PDBQT",
    requiresProject: true,
  },
  {
    id: "viewer",
    label: "3D 查看 / Box",
    description: "查看结构、Box 和 docking pose",
    requiresProject: true,
  },
  {
    id: "vina-config",
    label: "Vina 运行",
    description: "设置参数、生成 config、准备并执行 run",
    requiresProject: true,
  },
  {
    id: "result",
    label: "结果报告",
    description: "解析结果并导出 Markdown 报告",
    requiresProject: true,
  },
  {
    id: "help",
    label: "文档帮助",
    description: "查看新手流程、文件说明和科学边界",
  },
];

export const pageTitles: Record<PageId, string> = {
  home: "项目总览",
  "tool-check": "工具检测",
  "toolchain-status": "内置工具链状态",
  settings: "工具路径设置",
  "project-create": "创建项目",
  "structure-fetch": "获取原始结构文件",
  preparation: "准备 PDBQT",
  "import-pdbqt": "导入 PDBQT",
  "box-setup": "设置对接箱体",
  "vina-param": "设置 Vina 参数",
  "vina-config": "生成 Vina 配置",
  "run-prepare": "运行前检查",
  "run-execute": "执行 Vina",
  result: "查看结果",
  viewer: "3D 查看 / Box",
  report: "导出报告",
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
