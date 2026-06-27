import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import EmptyState from "../components/EmptyState";
import ErrorPanel from "../components/ErrorPanel";
import FilePathText from "../components/FilePathText";
import OnboardingGuide from "../components/OnboardingGuide";
import PageHeader from "../components/PageHeader";
import PreparedFileStatusCard from "../components/PreparedFileStatusCard";
import RawFileStatusCard from "../components/RawFileStatusCard";
import ReportStatusCard from "../components/ReportStatusCard";
import RunStatusCard from "../components/RunStatusCard";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import WorkflowStepper from "../components/WorkflowStepper";
import type { DockStartProject, ProjectWorkflowStatusResponse, ToolSource, ToolStatus, WorkflowFileStatus } from "../types";
import type { PageId } from "../navigation/pages";
import { buildWorkflowSteps } from "../utils/workflowSteps";

type ProjectDashboardPageProps = {
  project: DockStartProject | null;
  onNavigate: (page: PageId) => void;
  onProjectChange: (project: DockStartProject) => void;
  onWorkflowChange?: (workflow: ProjectWorkflowStatusResponse | null) => void;
};

type SummaryItem = {
  label: string;
  status: string;
  detail: string;
  tone: "ok" | "warning" | "error" | "muted" | "info";
};

type FirstRunToolchainStatus = {
  ok: boolean;
  vinaStatus: ToolStatus;
  vinaMessage: string;
  pythonStatus: ToolStatus;
  pythonSource: ToolSource;
  pythonPath: string;
  rdkitStatus: ToolStatus;
  meekoStatus: ToolStatus;
  nextAction: string;
  rawError: string;
};

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
  const activeVina = parsed.active_vina ?? {};
  const resolvedPython = parsed.resolved_python ?? {};
  const rdkit = parsed.rdkit_for_python ?? {};
  const meeko = parsed.meeko_for_python ?? {};
  const vinaStatus = (activeVina.status ?? "unknown") as ToolStatus;
  const pythonStatus = (resolvedPython.status ?? "unknown") as ToolStatus;
  const rdkitStatus = (rdkit.status ?? "unknown") as ToolStatus;
  const meekoStatus = (meeko.status ?? "unknown") as ToolStatus;
  const pythonSource = (parsed.python_source ?? resolvedPython.source ?? "unknown") as ToolSource;
  let nextAction = String(parsed.first_run_guidance?.recommended_action ?? "工具链状态已读取。可以创建项目，或先检查工具链详情。");
  if (vinaStatus !== "ok") {
    nextAction = "先配置 AutoDock Vina。没有 Vina 时无法执行 docking。";
  } else if (rdkitStatus !== "ok" || meekoStatus !== "ok") {
    nextAction = "如果要从 raw 文件自动准备 PDBQT，请先配置带 RDKit/Meeko 的 Python 环境。";
  } else if (pythonSource !== "configured" && pythonSource !== "bundled") {
    nextAction = "建议配置独立 conda Python 工具链，再创建项目。";
  }
  return {
    ok: Boolean(parsed.ok),
    vinaStatus,
    vinaMessage: String(activeVina.message ?? ""),
    pythonStatus,
    pythonSource,
    pythonPath: String(resolvedPython.path ?? ""),
    rdkitStatus,
    meekoStatus,
    nextAction,
    rawError: String(parsed.error?.raw_error ?? parsed.manifest_error ?? ""),
  };
}

function fileStatusLabel(status?: string): string {
  if (status === "ok") {
    return "已就绪";
  }
  if (status === "empty") {
    return "文件为空";
  }
  if (status === "error") {
    return "错误";
  }
  return "未就绪";
}

function fileTone(status?: string): SummaryItem["tone"] {
  if (status === "ok") {
    return "ok";
  }
  if (status === "missing") {
    return "warning";
  }
  return status ? "error" : "muted";
}

function summarizeFile(label: string, file?: WorkflowFileStatus): SummaryItem {
  return {
    label,
    status: fileStatusLabel(file?.status),
    detail: file?.path || "未记录",
    tone: fileTone(file?.status),
  };
}

function runSummary(latestRun: Record<string, unknown> | null | undefined): SummaryItem {
  if (!latestRun) {
    return {
      label: "latest run 状态",
      status: "未创建",
      detail: "尚未准备 Vina run",
      tone: "warning",
    };
  }
  const status = String(latestRun.status ?? "unknown");
  return {
    label: "latest run 状态",
    status,
    detail: String(latestRun.run_id ?? "未记录 run_id"),
    tone: status === "finished" ? "ok" : status === "failed" ? "error" : "info",
  };
}

function toolStatusTone(status: ToolStatus): SummaryItem["tone"] {
  if (status === "ok") {
    return "ok";
  }
  if (status === "missing" || status === "unknown") {
    return "warning";
  }
  return "error";
}

function toolStatusText(status: ToolStatus): string {
  if (status === "ok") {
    return "已检测";
  }
  if (status === "missing") {
    return "未检测";
  }
  if (status === "error") {
    return "检测错误";
  }
  return "状态未知";
}

function sourceText(source: ToolSource): string {
  const labels: Record<ToolSource, string> = {
    bundled: "内置工具链",
    configured: "用户配置",
    auto: "PATH 自动检测",
    current_environment: "当前环境",
    frontend_dependency: "前端依赖",
    missing: "未找到",
    unknown: "未知来源",
  };
  return labels[source] ?? labels.unknown;
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
  const [firstRunToolchain, setFirstRunToolchain] = useState<FirstRunToolchainStatus | null>(null);
  const [firstRunError, setFirstRunError] = useState("");

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
      if (parsed.project) {
        onProjectChange(parsed.project);
      }
      if (!parsed.ok) {
        setErrorMessage(parsed.error?.message ?? "读取项目工作流状态失败。");
        setRawError(parsed.error?.raw_error ?? "");
      }
    } catch (error) {
      setErrorMessage("前端未能调用项目工作流状态命令。");
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
      setFirstRunToolchain(null);
      setFirstRunError("");
      return;
    }
    const loadFirstRunToolchain = async () => {
      try {
        const rawPayload = await invoke<string>("get_toolchain_status");
        setFirstRunToolchain(parseFirstRunToolchainStatus(rawPayload));
        setFirstRunError("");
      } catch (error) {
        setFirstRunToolchain(null);
        setFirstRunError(error instanceof Error ? error.message : String(error));
      }
    };
    void loadFirstRunToolchain();
  }, [project]);

  const summaryItems = useMemo<SummaryItem[]>(() => {
    if (!workflow) {
      return [];
    }
    return [
      summarizeFile("raw receptor", workflow.raw?.receptor),
      summarizeFile("raw ligand", workflow.raw?.ligand),
      summarizeFile("prepared receptor", workflow.prepared?.receptor),
      summarizeFile("prepared ligand", workflow.prepared?.ligand),
      {
        label: "Box 参数",
        status: workflow.box?.status === "ok" ? "已就绪" : "需检查",
        detail: workflow.box?.warnings?.join("；") || workflow.box?.error?.message || "center / size 已记录",
        tone: workflow.box?.status === "ok" ? "ok" : "error",
      },
      {
        label: "Vina 参数",
        status: workflow.vina?.status === "ok" ? "已就绪" : "需检查",
        detail: workflow.vina?.warnings?.join("；") || workflow.vina?.error?.message || "运行参数已记录",
        tone: workflow.vina?.status === "ok" ? "ok" : "error",
      },
      summarizeFile("config 状态", workflow.config),
      runSummary(workflow.latest_run),
      {
        label: "report 状态",
        status: workflow.latest_run?.status === "finished" ? "可导出" : "待生成",
        detail: workflow.latest_run?.status === "finished" ? "可进入结果页解析并导出 Markdown 报告" : "需要先完成 Vina run 和结果解析",
        tone: workflow.latest_run?.status === "finished" ? "info" : "warning",
      },
    ];
  }, [workflow]);

  const workflowSteps = useMemo(() => buildWorkflowSteps(project, workflow), [project, workflow]);

  if (!project) {
    return (
      <>
        <EmptyState
          title="还没有 DockStart 项目"
          description="先创建一个项目。项目会保存 raw 文件、prepared PDBQT、Vina config、run metadata、结果和报告。"
          action={
            <>
              <ActionButton variant="primary" onClick={() => onNavigate("project-create")}>
                创建项目
              </ActionButton>
              <ActionButton onClick={() => onNavigate("help")}>查看新手帮助</ActionButton>
            </>
          }
        />
        <SectionCard
          title="首次启动工具链检查"
          description="这里只读取工具链状态，不安装软件，也不会运行 Vina、RDKit 或 Meeko preparation。"
        >
          {firstRunToolchain ? (
            <>
              <div className="dashboard-status-grid">
                <article className="dashboard-status-card">
                  <div>
                    <span>AutoDock Vina</span>
                    <StatusBadge tone={toolStatusTone(firstRunToolchain.vinaStatus)}>
                      {toolStatusText(firstRunToolchain.vinaStatus)}
                    </StatusBadge>
                  </div>
                  <p>{firstRunToolchain.vinaMessage || "未读取到说明。"}</p>
                </article>
                <article className="dashboard-status-card">
                  <div>
                    <span>Python 来源</span>
                    <StatusBadge tone={firstRunToolchain.pythonSource === "configured" ? "ok" : "info"}>
                      {sourceText(firstRunToolchain.pythonSource)}
                    </StatusBadge>
                  </div>
                  <p>{firstRunToolchain.pythonPath || "未读取到 Python 路径。"}</p>
                </article>
                <article className="dashboard-status-card">
                  <div>
                    <span>RDKit</span>
                    <StatusBadge tone={toolStatusTone(firstRunToolchain.rdkitStatus)}>
                      {toolStatusText(firstRunToolchain.rdkitStatus)}
                    </StatusBadge>
                  </div>
                  <p>自动准备 PDBQT 需要 RDKit 可用；DockStart 不会自动安装。</p>
                </article>
                <article className="dashboard-status-card">
                  <div>
                    <span>Meeko</span>
                    <StatusBadge tone={toolStatusTone(firstRunToolchain.meekoStatus)}>
                      {toolStatusText(firstRunToolchain.meekoStatus)}
                    </StatusBadge>
                  </div>
                  <p>受体/配体 preparation 依赖 Meeko；生成结果仍需人工检查。</p>
                </article>
              </div>
              <div className="next-action-card">
                <strong>{firstRunToolchain.nextAction}</strong>
                <p>新手建议先让 Vina 和 Python/RDKit/Meeko 状态清楚，再创建项目。</p>
              </div>
              <div className="toolbar">
                <ActionButton onClick={() => onNavigate("toolchain-status")}>查看工具链状态</ActionButton>
                <ActionButton onClick={() => onNavigate("settings")}>配置工具路径</ActionButton>
                <ActionButton variant="primary" onClick={() => onNavigate("project-create")}>创建项目</ActionButton>
              </div>
            </>
          ) : (
            <div className="warning-note">
              {firstRunError
                ? `当前不是 Tauri 桌面环境，或暂时无法读取工具链状态：${firstRunError}`
                : "正在读取工具链状态..."}
            </div>
          )}
        </SectionCard>
        <SectionCard title="第一次使用可以这样走" description="这是前端引导，不会自动运行任何外部工具。">
          <OnboardingGuide onNavigate={onNavigate} />
        </SectionCard>
      </>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="ProjectDashboardPage"
        title={project.project_name || "DockStart 项目"}
        description="从这里查看项目状态、确认下一步，并进入各个工作流页面。"
        actions={
          <>
            <ActionButton onClick={() => void loadWorkflow()}>{isBusy ? "刷新中..." : "刷新状态"}</ActionButton>
            <ActionButton onClick={() => onNavigate("project-create")}>创建新项目</ActionButton>
          </>
        }
      />

      <SectionCard title="项目基本信息">
        <div className="dashboard-meta-grid">
          <div>
            <span>project name</span>
            <strong>{project.project_name}</strong>
          </div>
          <div>
            <span>project dir</span>
            <FilePathText value={project.project_dir} />
          </div>
          <div>
            <span>created_at</span>
            <strong>{project.created_at || "未记录"}</strong>
          </div>
          <div>
            <span>updated_at</span>
            <strong>{project.updated_at || "未记录"}</strong>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="下一步推荐" description="来自后端 get_project_workflow_status，不做额外科学判断。">
        <div className="next-action-card">
          <strong>{workflow?.next_recommended_action || "正在读取项目状态..."}</strong>
          {workflow?.viewer?.recommended_viewer_action ? <p>{workflow.viewer.recommended_viewer_action}</p> : null}
        </div>
      </SectionCard>

      <SectionCard title="DockStart 工作流" description="按状态推导当前可做步骤，帮助新手减少来回猜页面。">
        <WorkflowStepper
          steps={workflowSteps}
          onAction={(step) => {
            if (step.targetPage) {
              onNavigate(step.targetPage as PageId);
            }
          }}
        />
      </SectionCard>

      <SectionCard title="工作流总览">
        <div className="unified-status-grid">
          <RawFileStatusCard title="raw receptor" file={workflow?.raw?.receptor} />
          <RawFileStatusCard title="raw ligand" file={workflow?.raw?.ligand} />
          <PreparedFileStatusCard title="prepared receptor" file={workflow?.prepared?.receptor} />
          <PreparedFileStatusCard title="prepared ligand" file={workflow?.prepared?.ligand} />
          <RunStatusCard
            runId={workflow?.latest_run?.run_id ? String(workflow.latest_run.run_id) : ""}
            status={workflow?.latest_run?.status ? String(workflow.latest_run.status) : "missing"}
          />
          <ReportStatusCard
            status={workflow?.latest_run?.status === "finished" ? "ready" : "missing"}
            path={workflow?.latest_run?.status === "finished" ? "reports/docking_report.md" : ""}
          />
        </div>
        <div className="dashboard-status-grid">
          {summaryItems.map((item) => (
            <article className="dashboard-status-card" key={item.label}>
              <div>
                <span>{item.label}</span>
                <StatusBadge tone={item.tone}>{item.status}</StatusBadge>
              </div>
              <p>{item.detail}</p>
            </article>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="快捷操作">
        <div className="action-card-grid">
          <button className="action-card" type="button" onClick={() => onNavigate("structure-fetch")}>
            <strong>下载原始结构</strong>
            <span>获取或管理 raw receptor / ligand</span>
          </button>
          <button className="action-card" type="button" onClick={() => onNavigate("preparation")}>
            <strong>准备 PDBQT</strong>
            <span>检查工具链并生成 prepared PDBQT</span>
          </button>
          <button className="action-card" type="button" onClick={() => onNavigate("box-setup")}>
            <strong>设置 Box</strong>
            <span>编辑 docking box 参数</span>
          </button>
          <button className="action-card" type="button" onClick={() => onNavigate("viewer")}>
            <strong>打开 3D 查看</strong>
            <span>查看结构、Box 和 docking pose</span>
          </button>
          <button className="action-card" type="button" onClick={() => onNavigate("vina-config")}>
            <strong>生成 config</strong>
            <span>预览并生成 configs/vina_config.txt</span>
          </button>
          <button className="action-card" type="button" onClick={() => onNavigate("run-prepare")}>
            <strong>运行 Vina</strong>
            <span>检查、准备 run 并执行</span>
          </button>
          <button className="action-card" type="button" onClick={() => onNavigate("result")}>
            <strong>查看结果报告</strong>
            <span>解析 scores 并导出 Markdown 报告</span>
          </button>
          <button className="action-card" type="button" onClick={() => onNavigate("help")}>
            <strong>查看帮助</strong>
            <span>新手流程、文件类型和科学边界说明</span>
          </button>
        </div>
      </SectionCard>

      <SectionCard title="风险提示">
        <div className="dashboard-risk-grid">
          <ScientificDisclaimer kind="score" />
          <ScientificDisclaimer kind="preparation" />
        </div>
      </SectionCard>

      <ErrorPanel error={workflow?.error ?? null} message={errorMessage} />
      {rawError ? (
        <details className="raw-error">
          <summary>查看 raw_error</summary>
          <pre>{rawError}</pre>
        </details>
      ) : null}
    </>
  );
}
