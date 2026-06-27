import type { DockStartProject } from "../types";
import type { PageId } from "../navigation/pages";

export function getWorkflowSummary(project: DockStartProject | null, page: PageId): string {
  if (!project) {
    return page === "project-create" ? "正在创建项目" : "未加载项目";
  }
  if (page === "result" || page === "report") {
    return "结果与报告阶段";
  }
  if (page === "run-prepare" || page === "run-execute") {
    return "Vina 运行阶段";
  }
  if (page === "viewer" || page === "box-setup") {
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
