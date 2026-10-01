import type { AppCapabilityProfile, DockStartProject, ProjectWorkflowStatusResponse } from "../types";
import type { PageId } from "../navigation/pages";
import { taskIntentFromProject, workflowRunForTask } from "./vinaTask";
import { isSameProjectDir } from "./backgroundProjectRefresh";

export type HelpRecommendation = { title: string; description: string; action: string; page: PageId; topic: string; runId?: string };
export function recommendHelp(project: DockStartProject | null, workflow: ProjectWorkflowStatusResponse | null, profile: AppCapabilityProfile | null): HelpRecommendation {
  if (!project) {
    if (profile?.ok && !profile.basic_mode_available) return { title: "先检查 AutoDock Vina", description: "尚未检测到最低对接能力。你仍可创建项目或查看可用示例，执行计算前需修复工具链。", action: "检查工具链", page: "toolchain-status", topic: "toolchain" };
    return { title: "创建项目，或先体验示例", description: "已有 PDBQT 可以直接使用；原始结构需要兼容的准备工具。", action: "创建项目", page: "project-create", topic: "start" };
  }
  if (!workflow?.ok || !isSameProjectDir(workflow.project_dir, project.project_dir)) return { title: "查看当前项目状态", description: "当前状态尚未确认，刷新后可获得下一步建议；也可直接前往项目页。", action: "打开项目", page: "home", topic: "errors" };
  const run = workflowRunForTask(workflow, taskIntentFromProject(project));
  if (run?.status === "running" || run?.status === "cancelling") return { title: "当前任务正在运行", description: "切换帮助页面不会终止计算；前往执行页查看进度或取消。", action: "查看运行进度", page: "run-execute", topic: "errors", runId: run.run_id };
  if (run?.status === "failed" || run?.status === "interrupted" || run?.status === "cancelled") return { title: "检查本次运行记录", description: "保留日志与输入快照，查明失败或中断原因后再创建新运行。", action: "查看运行与日志", page: "run-execute", topic: "errors", runId: run.run_id };
  if (run?.status === "finished") return { title: "查看构象、评分和结果分析", description: "打开当前任务的成功运行，复核姿势与科学边界后导出实验记录。", action: "查看结果", page: "result", topic: "results", runId: run.run_id };
  if (workflow.preparation?.receptor?.status === "running" || workflow.preparation?.ligand?.status === "running") return { title: "结构正在准备", description: "前往结构准备页查看当前进度与警告。", action: "查看准备进度", page: "preparation", topic: "structure" };
  if (workflow.prepared?.receptor?.status !== "ok" || workflow.prepared?.ligand?.status !== "ok") return { title: "补齐受体与配体 PDBQT", description: profile?.ok && !profile.assisted_mode_available ? "导入已有有效 PDBQT，或先配置 RDKit/Meeko 再转换原始结构。" : "按各文件的实际格式导入或转换，完成后检查准备警告。", action: "前往结构准备", page: "preparation", topic: "structure" };
  if (profile?.ok && !profile.basic_mode_available) return { title: "修复对接工具", description: "输入文件已就绪，但 Vina 当前不可用。", action: "检查工具链", page: "toolchain-status", topic: "toolchain" };
  const autobox = project.docking_protocol?.autobox && taskIntentFromProject(project) !== "dock" && project.docking_protocol?.engine !== "ad4_maps";
  if (!autobox && workflow.box?.status !== "ok") return { title: "确认搜索范围", description: "根据目标位点设置 Box 中心和尺寸，并在 3D 视图中复核。", action: "设置搜索范围", page: "run-prepare", topic: "box" };
  return { title: "复核参数和运行条件", description: "在运行工作台检查当前任务、评分协议及所有阻塞项，通过后再执行。", action: "打开运行工作台", page: "run-prepare", topic: "parameters" };
}
