import ActionButton from "../components/ActionButton";
import EmptyState from "../components/EmptyState";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
import type { PageId } from "../navigation/pages";
import type { DockStartProject, VinaRunMode } from "../types";

type RunRequiredPageProps = {
  project: DockStartProject;
  requestedPage: "run-execute" | "result" | "report";
  onNavigate: (page: PageId) => void;
};

function projectRunMode(project: DockStartProject): VinaRunMode {
  const value = project.docking_protocol?.run_mode;
  return value === "score_only" || value === "local_only" ? value : "dock";
}

function pageText(
  requestedPage: RunRequiredPageProps["requestedPage"],
  runMode: VinaRunMode,
): { title: string; description: string } {
  if (requestedPage === "run-execute") {
    return {
      title: "还没有可执行的 run",
      description: "需要先生成运行配置，并在运行前检查页创建运行记录，才能进入执行页。",
    };
  }
  if (requestedPage === "result") {
    return {
      title: "还没有可解析的 run",
      description: runMode === "dock"
        ? "需要先准备并执行一个 run，状态为 finished 后才能解析 scores.csv。"
        : "需要先准备并执行姿势评价，状态为 finished 后才能解析 evaluation.json。",
    };
  }
  return {
    title: "还没有可导出的报告",
    description: runMode === "dock"
      ? "需要先完成 run 并生成 scores.csv，然后才能生成 Markdown 结果分析报告。"
      : "需要先完成姿势评价并生成 evaluation.json，然后才能生成 Markdown 评价报告。",
  };
}

export default function RunRequiredPage({ project, requestedPage, onNavigate }: RunRequiredPageProps) {
  const runMode = projectRunMode(project);
  const text = pageText(requestedPage, runMode);

  return (
    <section className="project-page">
      <VinaWorkflowBar
        current={requestedPage === "run-execute" ? "execute" : requestedPage === "result" ? "result" : "report"}
        runMode={runMode}
      />
      <EmptyState
        title={text.title}
        description={`${text.description} 项目：${project.project_name}`}
        action={
          <>
            <ActionButton variant="primary" onClick={() => onNavigate("vina-config")}>
              从生成运行配置开始
            </ActionButton>
            <ActionButton onClick={() => onNavigate("run-prepare")}>进入运行前检查</ActionButton>
            <ActionButton onClick={() => onNavigate("home")}>回到项目总览</ActionButton>
          </>
        }
      />
    </section>
  );
}
