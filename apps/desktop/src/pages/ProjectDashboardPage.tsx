import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import ErrorPanel from "../components/ErrorPanel";
import FilePathText from "../components/FilePathText";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import type { NavigateHandler, PageId } from "../navigation/pages";
import type {
  DockStartProject,
  ProjectWorkflowStatusResponse,
  ToolStatus,
  ToolchainStatusResponse,
  WorkflowFileStatus,
} from "../types";

type ProjectDashboardPageProps = {
  project: DockStartProject | null;
  onNavigate: NavigateHandler;
  onProjectChange: (project: DockStartProject) => void;
  onWorkflowChange?: (workflow: ProjectWorkflowStatusResponse | null) => void;
};

type UiState = "未开始" | "可进行" | "进行中" | "已完成" | "缺失" | "失败" | "需检查";
type StepperState = "not-started" | "active" | "done";
type FirstRunToolchainSummary = {
  vinaStatus: ToolStatus;
  pythonStatus: ToolStatus;
  rdkitStatus: ToolStatus;
  meekoStatus: ToolStatus;
};

const dockingStepperSteps = ["准备结构", "搜索范围", "运行对接", "查看结果"];

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

function fileReady(file?: WorkflowFileStatus): boolean {
  return file?.status === "ok";
}

function parseToolchainSummary(rawPayload: string): FirstRunToolchainSummary {
  const parsed = JSON.parse(rawPayload) as Partial<ToolchainStatusResponse>;
  return {
    vinaStatus: parsed.active_vina?.status ?? "unknown",
    pythonStatus: parsed.resolved_python?.status ?? "unknown",
    rdkitStatus: parsed.rdkit_for_python?.status ?? "unknown",
    meekoStatus: parsed.meeko_for_python?.status ?? "unknown",
  };
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
      text: "搜索范围中心与尺寸",
      target: "run-prepare",
    },
    {
      title: "4 运行对接",
      state: run,
      text: "配置、记录、执行",
      target: "run-prepare",
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

function toolStatusClass(status: ToolStatus | undefined, pending: boolean): string {
  if (pending) return "checking";
  if (status === "ok") return "ready";
  if (status === "error") return "error";
  if (status === "missing") return "warning";
  return "muted";
}

function preparationStatusText(toolchain: FirstRunToolchainSummary | null, pending: boolean): string {
  if (pending) return "检测中";
  const rdkitReady = toolchain?.rdkitStatus === "ok";
  const meekoReady = toolchain?.meekoStatus === "ok";
  if (rdkitReady && meekoReady) return "可用";
  if (rdkitReady || meekoReady) return "需要确认";
  return "需要配置";
}

function preparationStatusClass(toolchain: FirstRunToolchainSummary | null, pending: boolean): string {
  if (pending) return "checking";
  const rdkitReady = toolchain?.rdkitStatus === "ok";
  const meekoReady = toolchain?.meekoStatus === "ok";
  if (rdkitReady && meekoReady) return "ready";
  if (rdkitReady || meekoReady) return "warning";
  return "warning";
}

function vinaStatusSummary(status: ToolStatus | undefined, pending: boolean): string {
  if (pending) return "检测中 · 运行对接前会再次检查";
  if (status === "ok") return "可用 · 可运行对接";
  if (status === "error") return "不可用 · 运行对接前需要配置";
  if (status === "missing") return "需要配置 · 运行对接前确认";
  return "需要确认 · 运行对接前确认";
}

function pythonStatusSummary(status: ToolStatus | undefined, pending: boolean): string {
  if (pending) return "检测中 · 仅影响 PDB/SDF 自动准备";
  if (status === "ok") return "可用 · 可处理 PDB/SDF";
  if (status === "error") return "不可用 · 影响 PDB/SDF";
  if (status === "missing") return "需要配置 · 影响 PDB/SDF";
  return "需要确认 · 影响 PDB/SDF";
}

function preparationStatusSummary(toolchain: FirstRunToolchainSummary | null, pending: boolean): string {
  const state = preparationStatusText(toolchain, pending);
  if (pending) return `${state} · 仅影响结构转换`;
  if (state === "可用") return "可用 · 可转换结构";
  return "需要确认 · 影响结构转换";
}

function stepperState(index: number, activeIndex: number | null): StepperState {
  if (activeIndex === null) return "not-started";
  if (index < activeIndex) return "done";
  if (index === activeIndex) return "active";
  return "not-started";
}

function projectStepperIndex(workflow: ProjectWorkflowStatusResponse | null): number {
  const receptorPrepared = fileReady(workflow?.prepared?.receptor);
  const ligandPrepared = fileReady(workflow?.prepared?.ligand);
  if (!(receptorPrepared && ligandPrepared)) return 0;
  if (workflow?.box?.status !== "ok") return 1;
  if (String(workflow?.latest_run?.status ?? "") !== "finished") return 2;
  return 3;
}

function DockingStepper({ activeIndex }: { activeIndex: number | null }) {
  return (
    <ol className="first-run-stepper" aria-label="对接流程进度">
      {dockingStepperSteps.map((label, index) => {
        const state = stepperState(index, activeIndex);
        return (
          <li className={state} key={label}>
            <span aria-hidden="true">{index + 1}</span>
            <strong>{label}</strong>
          </li>
        );
      })}
    </ol>
  );
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
  const [firstRunToolchain, setFirstRunToolchain] = useState<FirstRunToolchainSummary | null>(null);
  const [toolchainChecked, setToolchainChecked] = useState(false);
  const projectDir = project?.project_dir;

  const loadWorkflow = useCallback(async () => {
    if (!projectDir) {
      setWorkflow(null);
      onWorkflowChange?.(null);
      return;
    }
    setIsBusy(true);
    setErrorMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("get_project_workflow_status", {
        projectDir,
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
  }, [onProjectChange, onWorkflowChange, projectDir]);

  useEffect(() => {
    void loadWorkflow();
  }, [loadWorkflow]);

  useEffect(() => {
    if (project) {
      setFirstRunToolchain(null);
      setToolchainChecked(false);
      return;
    }
    let cancelled = false;
    const loadToolchain = async () => {
      setToolchainChecked(false);
      try {
        const rawPayload = await invoke<string>("get_toolchain_status");
        if (!cancelled) {
          setFirstRunToolchain(parseToolchainSummary(rawPayload));
        }
      } catch {
        if (!cancelled) {
          setFirstRunToolchain(null);
        }
      } finally {
        if (!cancelled) {
          setToolchainChecked(true);
        }
      }
    };
    void loadToolchain();
    return () => {
      cancelled = true;
    };
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
    const toolchainPending = !toolchainChecked;
    return (
      <PageShell className="first-run-landing" labelledBy="first-run-title">
        <PageHero
          title="新建对接项目"
          titleId="first-run-title"
          description="导入受体和配体，设置搜索范围，然后运行 AutoDock Vina。"
          actions={
            <ActionButton variant="text" onClick={() => onNavigate("project-create")}>
              打开已有项目
            </ActionButton>
          }
        />

        <BodyGrid className="first-run-workspace">
          <MainPanel className="first-run-main-panel">
            <div className="main-panel-content">
              <section className="first-run-stepper-shell" aria-label="对接流程">
                <p className="first-run-stepper-status">未开始</p>
                <DockingStepper activeIndex={null} />
              </section>

              <section className="start-route-section" aria-labelledby="start-route-title">
                <div className="start-route-heading">
                  <h2 id="start-route-title">选择开始方式</h2>
                </div>
                <div className="start-route-grid">
                  <button className="start-route-card" data-layout="task-card" type="button" onClick={() => onNavigate("project-create", { startMode: "basic" })}>
                    <div className="start-route-card-copy">
                      <h3>已准备好的对接文件</h3>
                      <p>已有 PDBQT 格式的受体和配体。</p>
                    </div>
                    <span className="secondary-button start-route-button start-route-button-proxy">选择 PDBQT 文件</span>
                  </button>

                  <button
                    className="start-route-card"
                    data-layout="task-card"
                    type="button"
                    onClick={() => onNavigate("project-create", { startMode: "assisted" })}
                  >
                    <div className="start-route-card-copy">
                      <h3>从原始结构开始</h3>
                      <p>Assisted Stable 已随附准备工具链，可直接导入 PDB / SDF。</p>
                    </div>
                    <span className="secondary-button start-route-button start-route-button-proxy">导入 PDB / SDF</span>
                  </button>

                  <button className="start-route-card" data-layout="task-card" type="button" onClick={() => onNavigate("project-create", { startMode: "demo" })}>
                    <div className="start-route-card-copy">
                      <h3>打开示例流程</h3>
                      <p>使用内置示例完成一次对接。</p>
                    </div>
                    <span className="secondary-button start-route-button start-route-button-proxy">打开示例</span>
                  </button>
                </div>
              </section>

              <p className="first-run-storage-note">
                DockStart 项目将保存受体、配体、搜索范围、配置文件、运行日志和结果报告。
              </p>
              </div>
          </MainPanel>

          <RightRail className="first-run-side-rail">
            <RightRailSection title="当前状态">
              <dl className="side-rail-list">
                <div>
                  <dt>项目</dt>
                  <dd>未加载项目</dd>
                </div>
                <div>
                  <dt>下一步</dt>
                  <dd>选择一种开始方式</dd>
                </div>
              </dl>
            </RightRailSection>

            <RightRailSection title="工具链">
              <dl className="side-rail-list toolchain-summary-list">
                <div>
                  <dt>Vina</dt>
                  <dd className={toolStatusClass(firstRunToolchain?.vinaStatus, toolchainPending)}>
                    {vinaStatusSummary(firstRunToolchain?.vinaStatus, toolchainPending)}
                  </dd>
                </div>
                <div>
                  <dt>Python</dt>
                  <dd className={toolStatusClass(firstRunToolchain?.pythonStatus, toolchainPending)}>
                    {pythonStatusSummary(firstRunToolchain?.pythonStatus, toolchainPending)}
                  </dd>
                </div>
                <div>
                  <dt>RDKit / Meeko</dt>
                  <dd className={preparationStatusClass(firstRunToolchain, toolchainPending)}>
                    {preparationStatusSummary(firstRunToolchain, toolchainPending)}
                  </dd>
                </div>
              </dl>
            </RightRailSection>

            <RightRailSection title="最近项目">
              <p className="side-rail-muted">暂无最近项目</p>
            </RightRailSection>

            <RightRailSection title="快速帮助">
              <div className="side-rail-help">
                <p>
                  <strong>已有 receptor.pdbqt 和 ligand.pdbqt？</strong>
                  <span>选择“已准备好的对接文件”。</span>
                </p>
                <p>
                  <strong>只有 PDB 或 SDF？</strong>
                  <span>选择“从原始结构开始”。</span>
                </p>
              </div>
            </RightRailSection>
          </RightRail>
        </BodyGrid>
      </PageShell>
    );
  }

  const dashboardStepperIndex = projectStepperIndex(workflow);

  return (
    <PageShell labelledBy="project-dashboard-title">
      <PageHero
        eyebrow="项目总览"
        title={project.project_name || "DockStart 项目"}
        titleId="project-dashboard-title"
        description={workflow?.next_recommended_action || "读取项目状态后会给出下一步。"}
        actions={
          <>
          <ActionButton onClick={() => void loadWorkflow()}>{isBusy ? "刷新中..." : "刷新状态"}</ActionButton>
          <ActionButton onClick={() => onNavigate("project-create")}>创建项目</ActionButton>
          <ActionButton variant="primary" onClick={() => onNavigate(nextPage)}>继续当前步骤</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <FilePathText value={project.project_dir} />

            <section className="dashboard-progress-strip" aria-label="对接流程状态">
              <p className="first-run-stepper-status active">
                第 {dashboardStepperIndex + 1} 步 / 共 4 步：{dockingStepperSteps[dashboardStepperIndex]}
              </p>
              <DockingStepper activeIndex={dashboardStepperIndex} />
            </section>

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
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="当前状态">
            <dl className="mode-context-list">
              <div>
                <dt>当前步骤</dt>
                <dd>{dockingStepperSteps[dashboardStepperIndex]}</dd>
              </div>
              <div>
                <dt>下一步</dt>
                <dd>{workflow?.next_recommended_action || "继续当前步骤"}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="项目目录">
            <FilePathText value={project.project_dir} />
          </RightRailSection>

          <RightRailSection title="提示">
            <p>工作流卡片可直接进入对应步骤；右侧信息只显示当前项目的辅助状态。</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
