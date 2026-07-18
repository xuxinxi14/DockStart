import ActionButton from "../components/ActionButton";
import EmptyState from "../components/EmptyState";
import VinaWorkflowBar from "../components/VinaWorkflowBar";
import type { PageId } from "../navigation/pages";
import type { DockStartProject } from "../types";

type RunRequiredPageProps = {
  project: DockStartProject;
  requestedPage: "run-execute" | "result" | "report";
  onNavigate: (page: PageId) => void;
};

const pageText: Record<RunRequiredPageProps["requestedPage"], { title: string; description: string }> = {
  "run-execute": {
    title: "还没有可执行的 run",
    description: "需要先生成运行配置，并在运行前检查页创建运行记录，才能进入执行页。",
  },
  result: {
    title: "还没有可解析的 run",
    description: "需要先准备并执行一个 run，状态为 finished 后才能解析 scores.csv。",
  },
  report: {
    title: "还没有可导出的报告",
    description: "需要先完成 run 并生成 scores.csv，然后才能生成 Markdown 结果分析报告。",
  },
};

export default function RunRequiredPage({ project, requestedPage, onNavigate }: RunRequiredPageProps) {
  const text = pageText[requestedPage];

  return (
    <section className="project-page">
      <VinaWorkflowBar current={requestedPage === "run-execute" ? "execute" : requestedPage === "result" ? "result" : "report"} />
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
