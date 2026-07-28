import type { DockStartProject } from "../types";
import type { PageId } from "../navigation/pages";

export function getWorkflowSummary(project: DockStartProject | null, page: PageId): string {
  if (!project) {
    return page === "project-create" ? "正在创建项目" : "未加载项目";
  }
  if (page === "result" || page === "report") {
    return "结果与报告阶段";
  }
  if (page === "hydrated-ad4") {
    return "实验性水合 AD4 工作流";
  }
  if (page === "run-prepare" || page === "run-execute") {
    return project.docking_protocol?.engine === "ad4_maps"
      ? "AutoDock4 maps 运行阶段"
      : "Vina 运行阶段";
  }
  if (page === "box-setup") {
    return "结构与 Box 阶段";
  }
  if (page === "preparation" || page === "import-pdbqt") {
    return "PDBQT 准备阶段";
  }
  if (page === "structure-fetch") {
    return "原始结构阶段";
  }
  return "项目已加载";
}
