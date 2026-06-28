import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import EmptyState from "../components/EmptyState";
import ErrorPanel from "../components/ErrorPanel";
import FilePathText from "../components/FilePathText";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import type { PageId } from "../navigation/pages";
import type {
  DockStartProject,
  ProjectWorkflowStatusResponse,
  ToolSource,
  ToolStatus,
  WorkflowFileStatus,
} from "../types";

type ProjectDashboardPageProps = {
  project: DockStartProject | null;
  onNavigate: (page: PageId) => void;
  onProjectChange: (project: DockStartProject) => void;
  onWorkflowChange?: (workflow: ProjectWorkflowStatusResponse | null) => void;
};

type FirstRunToolchainStatus = {
  vinaStatus: ToolStatus;
  pythonStatus: ToolStatus;
  pythonSource: ToolSource;
  rdkitStatus: ToolStatus;
  meekoStatus: ToolStatus;
};

type UiState = "未开始" | "可进行" | "进行中" | "已完成" | "缺失" | "失败" | "需检查";

function parseWorkflowStatus(rawPayload: string): ProjectWorkflowStatusResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectWorkflowStatusResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir ?? "",
    project: parsed.project ?? null,
    raw: parsed.raw,
    prepared: parsed.prepared,
    preparation: parsed.preparation,
    box: parsed.box,
    vina: parsed.vina,
    config: parsed.config,
    latest_run: parsed.latest_run ?? null,
    viewer: parsed.viewer,
    next_recommended_action: parsed.next_recommended_action,
    message: parsed.message,
    error: parsed.error ?? null,
  };
}

function parseFirstRunToolchainStatus(rawPayload: string): FirstRunToolchainStatus {
  const parsed = JSON.parse(rawPayload) as Record<string, any>;
  return {
    vinaStatus: (parsed.active_vina?.status ?? "unknown") as ToolStatus,
    pythonStatus: (parsed.resolved_python?.status ?? "unknown") as ToolStatus,
    pythonSource: (parsed.python_source ?? parsed.resolved_python?.source ?? "unknown") as ToolSource,
    rdkitStatus: (parsed.rdkit_for_python?.status ?? "unknown") as ToolStatus,
    meekoStatus: (parsed.meeko_for_python?.status ?? "unknown") as ToolStatus,
  };
}

function fileReady(file?: WorkflowFileStatus): boolean {
  return file?.status === "ok";
}

function statusTone(state: UiState): "ok" | "warning" | "error" | "muted" | "info" {
  if (state === "已完成" || state === "可进行") {
    return "ok";
  }
  if (state === "失败") {
    return "error";
  }
  if (state === "缺失" || state === "需检查") {
    return "warning";
  }
  if (state === "进行中") {
    return "info";
  }
  return "muted";
}

function toolText(status: ToolStatus): string {
  if (status === "ok") return "可用";
  if (status === "missing") return "缺失";
  if (status === "error") return "失败";
  return "需检查";
}

function sourceText(source: ToolSource): string {
  if (source === "bundled") return "内置";
  if (source === "configured") return "已配置";
  if (source === "auto") return "PATH";
  if (source === "current_environment") return "当前环境";
  return "未确认";
}

function fileState(file?: WorkflowFileStatus): UiState {
  if (file?.status === "ok") return "已完成";
  if (file?.status === "empty" || file?.status === "error") return "需检查";
  return "缺失";
}

function runState(workflow: ProjectWorkflowStatusResponse | null): UiState {
  const status = String(workflow?.latest_run?.status ?? "");
  if (!workflow?.latest_run) return workflow?.config?.status === "ok" ? "可进行" : "未开始";
  if (status === "finished") return "已完成";
  if (status === "failed") return "失败";
  if (status === "running") return "进行中";
  return "可进行";
}

function workflowRows(workflow: ProjectWorkflowStatusResponse | null): Array<{
  title: string;
  state: UiState;
  text: string;
  target: PageId;
}> {
  const receptorRaw = fileReady(workflow?.raw?.receptor);
  const ligandRaw = fileReady(workflow?.raw?.ligand);
  const receptorPrepared = fileReady(workflow?.prepared?.receptor);
  const ligandPrepared = fileReady(workflow?.prepared?.ligand);
  const run = runState(workflow);
  return [
    {
      title: "1 获取结构",
      state: receptorRaw && ligandRaw ? "已完成" : receptorRaw || ligandRaw ? "需检查" : "可进行",
      text: "受体 / 配体 raw 文件",
      target: "structure-fetch",
    },
    {
      title: "2 准备 Vina 输入",
      state: receptorPrepared && ligandPrepared ? "已完成" : receptorRaw || ligandRaw ? "可进行" : "缺失",
      text: "prepared receptor / ligand PDBQT",
      target: "preparation",
    },
    {
      title: "3 设置搜索范围",
      state: workflow?.box?.status === "ok" ? "已完成" : "可进行",
      text: "Box 中心与尺寸",
      target: "box-setup",
    },
    {
      title: "4 运行对接",
      state: run,
      text: "配置、记录、执行",
      target: run === "未开始" ? "vina-config" : "run-prepare",
    },
    {
      title: "5 结果与报告",
      state: String(workflow?.latest_run?.status ?? "") === "finished" ? "可进行" : "未开始",
      text: "scores 与 Markdown 实验记录",
      target: "result",
    },
  ];
}

function nextTarget(workflow: ProjectWorkflowStatusResponse | null): PageId {
  const row = workflowRows(workflow).find((item) => item.state !== "已完成");
  return row?.target ?? "result";
}

function artifact(label: string, state: UiState, detail: string) {
  return { label, state, detail };
}

export default function ProjectDashboardPage({
  project,
  onNavigate,
  onProjectChange,
  onWorkflowChange,
}: ProjectDashboardPageProps) {
  const [workflow, setWorkflow] = useState<ProjectWorkflowStatusResponse | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [toolchain, setToolchain] = useState<FirstRunToolchainStatus | null>(null);

  const loadWorkflow = useCallback(async () => {
    if (!project) {
      setWorkflow(null);
      onWorkflowChange?.(null);
      return;
    }
    setIsBusy(true);
    setErrorMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("get_project_workflow_status", {
        projectDir: project.project_dir,
      });
      const parsed = parseWorkflowStatus(rawPayload);
      setWorkflow(parsed);
      onWorkflowChange?.(parsed);
      if (parsed.project) onProjectChange(parsed.project);
      if (!parsed.ok) {
        setErrorMessage(parsed.error?.message ?? "读取项目状态失败。");
        setRawError(parsed.error?.raw_error ?? "");
      }
    } catch (error) {
      setErrorMessage("无法读取项目状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [onProjectChange, onWorkflowChange, project]);

  useEffect(() => {
    void loadWorkflow();
  }, [loadWorkflow]);

  useEffect(() => {
    if (project) {
      setToolchain(null);
      return;
    }
    const loadToolchain = async () => {
      try {
        const rawPayload = await invoke<string>("get_toolchain_status");
        setToolchain(parseFirstRunToolchainStatus(rawPayload));
      } catch {
        setToolchain(null);
      }
    };
    void loadToolchain();
  }, [project]);

  const rows = useMemo(() => workflowRows(workflow), [workflow]);
  const nextPage = nextTarget(workflow);
  const artifacts = useMemo(
    () => [
      artifact("受体 raw", fileState(workflow?.raw?.receptor), workflow?.raw?.receptor?.path || "未记录"),
      artifact("配体 raw", fileState(workflow?.raw?.ligand), workflow?.raw?.ligand?.path || "未记录"),
      artifact("受体 PDBQT", fileState(workflow?.prepared?.receptor), workflow?.prepared?.receptor?.path || "未记录"),
      artifact("配体 PDBQT", fileState(workflow?.prepared?.ligand), workflow?.prepared?.ligand?.path || "未记录"),
      artifact(
        "latest run",
        runState(workflow),
        workflow?.latest_run?.run_id ? String(workflow.latest_run.run_id) : "未创建",
      ),
      artifact(
        "report",
        String(workflow?.latest_run?.status ?? "") === "finished" ? "可进行" : "未开始",
        String(workflow?.latest_run?.status ?? "") === "finished" ? "可导出实验记录" : "等待结果解析",
      ),
    ],
    [workflow],
  );

  if (!project) {
    return (
      <section className="workbench-page">
        <EmptyState
          title="开始一个 DockStart 项目"
          description="从结构获取或 PDBQT 导入开始，完成一次可复现的 AutoDock Vina 对接记录。"
          action={
            <>
              <ActionButton variant="primary" onClick={() => onNavigate("project-create")}>
                创建项目
              </ActionButton>
              <ActionButton disabled title="当前版本尚未提供打开已有项目入口">
                打开已有项目
              </ActionButton>
              <ActionButton onClick={() => onNavigate("help")}>查看流程</ActionButton>
            </>
          }
        />

        <SectionCard title="新手流程">
          <div className="compact-grid">
            {["配置工具链", "创建项目", "获取结构 / 导入 PDBQT", "运行并查看结果"].map((title, index) => (
              <article className="metric-card" key={title}>
                <span>步骤 {index + 1}</span>
                <strong>{title}</strong>
              </article>
            ))}
          </div>
        </SectionCard>

        <SectionCard title="工具链简况">
          {toolchain ? (
            <div className="status-strip">
              <article className="metric-card">
                <span>AutoDock Vina</span>
                <strong>{toolText(toolchain.vinaStatus)}</strong>
              </article>
              <article className="metric-card">
                <span>Python</span>
                <strong>{toolText(toolchain.pythonStatus)} · {sourceText(toolchain.pythonSource)}</strong>
              </article>
              <article className="metric-card">
                <span>RDKit / Meeko</span>
                <strong>{toolText(toolchain.rdkitStatus)} / {toolText(toolchain.meekoStatus)}</strong>
              </article>
            </div>
          ) : (
            <p className="message-line">尚未读取工具链状态。</p>
          )}
          <div className="button-row">
            <ActionButton onClick={() => onNavigate("toolchain-status")}>配置工具链</ActionButton>
            <ActionButton variant="primary" onClick={() => onNavigate("project-create")}>创建项目</ActionButton>
          </div>
        </SectionCard>
      </section>
    );
  }

  return (
    <section className="workbench-page">
      <header className="page-hero">
        <div className="page-hero-main">
          <p className="eyebrow">项目总览</p>
          <h1>{project.project_name || "DockStart 项目"}</h1>
          <p>{workflow?.next_recommended_action || "读取项目状态后会给出下一步。"}</p>
          <FilePathText value={project.project_dir} />
        </div>
        <div className="page-hero-actions">
          <ActionButton onClick={() => void loadWorkflow()}>{isBusy ? "刷新中..." : "刷新状态"}</ActionButton>
          <ActionButton onClick={() => onNavigate("project-create")}>创建项目</ActionButton>
          <ActionButton variant="primary" onClick={() => onNavigate(nextPage)}>继续当前步骤</ActionButton>
        </div>
      </header>

      <SectionCard title="工作流">
        <div className="dashboard-timeline">
          {rows.map((row) => (
            <button className="workflow-step action-card" key={row.title} type="button" onClick={() => onNavigate(row.target)}>
              <span>{row.title}</span>
              <strong>{row.text}</strong>
              <StatusBadge tone={statusTone(row.state)}>{row.state}</StatusBadge>
            </button>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="项目产物">
        <div className="compact-grid">
          {artifacts.map((item) => (
            <article className="file-card" key={item.label}>
              <span>{item.label}</span>
              <strong>{item.detail}</strong>
              <StatusBadge tone={statusTone(item.state)}>{item.state}</StatusBadge>
            </article>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="风险提示">
        <div className="two-column-grid">
          <ScientificDisclaimer kind="score" />
          <ScientificDisclaimer kind="preparation" />
        </div>
      </SectionCard>

      <ErrorPanel error={workflow?.error ?? null} message={errorMessage} />
      {rawError ? (
        <details className="technical-details">
          <summary>技术详情</summary>
          <pre>{rawError}</pre>
        </details>
      ) : null}
    </section>
  );
}
