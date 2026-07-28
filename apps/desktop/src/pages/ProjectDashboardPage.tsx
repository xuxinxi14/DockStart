import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import ErrorPanel from "../components/ErrorPanel";
import FilePathText from "../components/FilePathText";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import type { NavigateHandler, PageId, ProjectTaskIntent } from "../navigation/pages";
import type {
  DockStartProject,
  ProjectWorkflowStatusResponse,
  ToolStatus,
  ToolchainStatusResponse,
  WorkflowFileStatus,
} from "../types";
import { writeDockingWorkspaceMode } from "../utils/dockingMode";
import {
  projectTaskOptions,
  projectTaskSwitch,
  taskIntentFromProject,
  taskIntentLabel,
  workflowRunForTask,
} from "../utils/vinaTask";

type ProjectDashboardPageProps = {
  project: DockStartProject | null;
  onNavigate: NavigateHandler;
  onOpenProject: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onWorkflowChange?: (workflow: ProjectWorkflowStatusResponse | null) => void;
};

type UiState = "未开始" | "可进行" | "进行中" | "已完成" | "无需" | "缺失" | "失败" | "需检查";
type StepperState = "not-started" | "active" | "done";
type FirstRunToolchainSummary = {
  vinaStatus: ToolStatus;
  pythonStatus: ToolStatus;
  rdkitStatus: ToolStatus;
  meekoStatus: ToolStatus;
};

const dockingStepperSteps = ["准备结构", "搜索范围", "运行对接", "查看结果"];

type DashboardTaskCopy = {
  stepperSteps: string[];
  stepperLabel: string;
  rangeTitle: string;
  rangeText: string;
  runTitle: string;
  runText: string;
  resultText: string;
  resultArtifactLabel: string;
  resultArtifactDetail: string;
  reportWaiting: string;
  heroDescription: string;
};

function dashboardTaskCopy(
  intent: ProjectTaskIntent,
  autobox: boolean,
  isAd4Maps = false,
): DashboardTaskCopy {
  if (intent === "score_only") {
    return {
      stepperSteps: ["准备结构", "评价范围", "姿势评分", "查看结果"],
      stepperLabel: "姿势评分流程进度",
      rangeTitle: "3 设置评价范围",
      rangeText: isAd4Maps
        ? "使用当前 AutoDock4 maps 网格评价输入姿势"
        : autobox
          ? "围绕输入姿势自动建立评分网格"
          : "使用项目 Box 评价当前姿势",
      runTitle: "4 评价当前姿势",
      runText: "配置、能量项与运行记录",
      resultText: "evaluation.json 与 Markdown 实验记录",
      resultArtifactLabel: "评分结果",
      resultArtifactDetail: "evaluation.json 与能量项",
      reportWaiting: "等待姿势评分完成",
      heroDescription: "评价当前输入姿势的能量项，不执行构象或新位点搜索。",
    };
  }
  if (intent === "local_only") {
    return {
      stepperSteps: ["准备结构", "优化范围", "局部优化", "查看结果"],
      stepperLabel: "局部优化流程进度",
      rangeTitle: "3 设置优化范围",
      rangeText: isAd4Maps
        ? "使用当前 AutoDock4 maps 网格进行局部优化"
        : autobox
          ? "围绕输入姿势自动建立优化网格"
          : "使用项目 Box 进行局部优化",
      runTitle: "4 局部优化当前姿势",
      runText: "输入评分、优化后姿势与运行记录",
      resultText: "输入/优化后评分、位移与 Markdown 实验记录",
      resultArtifactLabel: "优化结果",
      resultArtifactDetail: "evaluation.json 与优化后 PDBQT",
      reportWaiting: "等待局部优化完成",
      heroDescription: "从当前输入姿势附近进行优化，不执行全局位点搜索。",
    };
  }
  return {
    stepperSteps: dockingStepperSteps,
    stepperLabel: "对接流程进度",
    rangeTitle: "3 设置搜索范围",
    rangeText: "搜索范围中心与尺寸",
    runTitle: "4 运行对接",
    runText: "配置、记录、执行",
    resultText: "scores、构象与 Markdown 实验记录",
    resultArtifactLabel: "对接结果",
    resultArtifactDetail: "scores.csv 与构象输出",
    reportWaiting: "等待对接完成",
    heroDescription: "准备结构、设置搜索范围并运行 AutoDock Vina。",
  };
}

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
    latest_run_for_current_mode: parsed.latest_run_for_current_mode,
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

function isTerminalState(state: UiState): boolean {
  return state === "已完成" || state === "无需";
}

function fileState(file?: WorkflowFileStatus): UiState {
  if (file?.status === "ok") return "已完成";
  if (file?.status === "empty" || file?.status === "error") return "需检查";
  return "缺失";
}

function runState(
  workflow: ProjectWorkflowStatusResponse | null,
  intent: ProjectTaskIntent = "dock",
): UiState {
  const modeRun = workflowRunForTask(workflow, intent);
  const status = String(modeRun?.status ?? "");
  if (!modeRun) return workflow?.config?.status === "ok" ? "可进行" : "未开始";
  if (status === "finished") return "已完成";
  if (status === "failed") return "失败";
  if (status === "running") return "进行中";
  return "可进行";
}

function workflowRows(
  workflow: ProjectWorkflowStatusResponse | null,
  intent: ProjectTaskIntent = "dock",
  autobox = false,
  isAd4Maps = false,
): Array<{
  title: string;
  state: UiState;
  text: string;
  target: PageId;
  runId?: string;
}> {
  const receptorRaw = fileReady(workflow?.raw?.receptor);
  const ligandRaw = fileReady(workflow?.raw?.ligand);
  const receptorPrepared = fileReady(workflow?.prepared?.receptor);
  const ligandPrepared = fileReady(workflow?.prepared?.ligand);
  const preparedInputsReady = receptorPrepared && ligandPrepared;
  const rawInputsReady = receptorRaw && ligandRaw;
  const rawStageSkipped = preparedInputsReady && !rawInputsReady;
  const modeRun = workflowRunForTask(workflow, intent);
  const run = runState(workflow, intent);
  const copy = dashboardTaskCopy(intent, autobox, isAd4Maps);
  const rangeReady = autobox || workflow?.box?.status === "ok";
  return [
    {
      title: "1 获取结构",
      state: rawStageSkipped ? "无需" : rawInputsReady ? "已完成" : receptorRaw || ligandRaw ? "需检查" : "可进行",
      text: rawStageSkipped ? "PDBQT 已就绪，跳过原始结构" : "受体 / 配体 raw 文件",
      target: "structure-fetch",
    },
    {
      title: "2 转换为 PDBQT",
      state: preparedInputsReady ? "已完成" : receptorRaw || ligandRaw ? "可进行" : "缺失",
      text: rawStageSkipped ? "PDBQT 已就绪，无需格式转换" : "prepared receptor / ligand PDBQT",
      target: "preparation",
    },
    {
      title: copy.rangeTitle,
      state: rangeReady ? "已完成" : "可进行",
      text: copy.rangeText,
      target: "run-prepare",
    },
    {
      title: copy.runTitle,
      state: run,
      text: copy.runText,
      target: modeRun ? "run-execute" : "run-prepare",
      runId: typeof modeRun?.run_id === "string" ? modeRun.run_id : undefined,
    },
    {
      title: "5 结果与报告",
      state: String(modeRun?.status ?? "") === "finished"
        ? "可进行"
        : "未开始",
      text: copy.resultText,
      target:
        String(modeRun?.status ?? "") === "finished"
          ? "result"
          : modeRun
            ? "run-execute"
            : "run-prepare",
      runId: typeof modeRun?.run_id === "string" ? modeRun.run_id : undefined,
    },
  ];
}

function nextTarget(
  workflow: ProjectWorkflowStatusResponse | null,
  intent: ProjectTaskIntent = "dock",
  autobox = false,
  isAd4Maps = false,
): PageId {
  const row = workflowRows(workflow, intent, autobox, isAd4Maps).find(
    (item) => !isTerminalState(item.state),
  );
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

function projectStepperIndex(
  workflow: ProjectWorkflowStatusResponse | null,
  autobox = false,
  intent: ProjectTaskIntent = "dock",
): number {
  const receptorPrepared = fileReady(workflow?.prepared?.receptor);
  const ligandPrepared = fileReady(workflow?.prepared?.ligand);
  if (!(receptorPrepared && ligandPrepared)) return 0;
  if (!autobox && workflow?.box?.status !== "ok") return 1;
  if (String(workflowRunForTask(workflow, intent)?.status ?? "") !== "finished") return 2;
  return 3;
}

function DockingStepper({
  activeIndex,
  labels = dockingStepperSteps,
  ariaLabel = "对接流程进度",
}: {
  activeIndex: number | null;
  labels?: string[];
  ariaLabel?: string;
}) {
  return (
    <ol className="first-run-stepper" aria-label={ariaLabel}>
      {labels.map((label, index) => {
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
  onOpenProject,
  onProjectChange,
  onWorkflowChange,
}: ProjectDashboardPageProps) {
  const [workflow, setWorkflow] = useState<ProjectWorkflowStatusResponse | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [firstRunToolchain, setFirstRunToolchain] = useState<FirstRunToolchainSummary | null>(null);
  const [toolchainChecked, setToolchainChecked] = useState(false);
  const [taskSwitching, setTaskSwitching] = useState<ProjectTaskIntent | null>(null);
  const projectDir = project?.project_dir;
  const taskIntent = taskIntentFromProject(project);
  const isAd4Maps = project?.docking_protocol?.engine === "ad4_maps";
  const taskAutobox =
    taskIntent !== "dock"
    && !isAd4Maps
    && project?.docking_protocol?.autobox === true;
  const taskSwitchBlockedByActiveRun =
    String(workflow?.latest_run?.status ?? "") === "running"
    || [
      "queued",
      "starting",
      "baseline_starting",
      "baseline_scoring",
      "local_starting",
      "local_optimizing",
      "cancelling",
      "cancel_pending",
    ].includes(String(workflow?.latest_run?.stage ?? ""));
  const taskCopy = dashboardTaskCopy(taskIntent, taskAutobox, isAd4Maps);

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

  const switchProjectTask = useCallback(async (requestedIntent: ProjectTaskIntent) => {
    if (
      !project
      || requestedIntent === taskIntent
      || taskSwitching
      || taskSwitchBlockedByActiveRun
    ) return;
    const selection = projectTaskSwitch(
      taskIntent,
      project.docking_protocol?.autobox,
      requestedIntent,
    );
    setTaskSwitching(requestedIntent);
    setErrorMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("update_vina_run_protocol", {
        projectDir: project.project_dir,
        runMode: selection.runMode,
        autobox: isAd4Maps ? false : selection.autobox,
        confirmPoseContext: selection.confirmPoseContext,
      });
      const response = JSON.parse(rawPayload) as {
        ok?: boolean;
        project?: DockStartProject | null;
        error?: { message?: string; raw_error?: string };
      };
      if (!response.ok || !response.project) {
        throw new Error(response.error?.message || "运行任务类型保存失败。");
      }
      if (selection.workspaceMode) {
        writeDockingWorkspaceMode(response.project.project_dir, selection.workspaceMode);
      }
      onProjectChange(response.project);
      onNavigate("run-prepare");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "运行任务类型保存失败。");
      setRawError(error instanceof Error ? error.stack ?? error.message : String(error));
    } finally {
      setTaskSwitching(null);
    }
  }, [
    isAd4Maps,
    onNavigate,
    onProjectChange,
    project,
    taskIntent,
    taskSwitchBlockedByActiveRun,
    taskSwitching,
  ]);

  const rows = useMemo(
    () => workflowRows(workflow, taskIntent, taskAutobox, isAd4Maps),
    [isAd4Maps, taskAutobox, taskIntent, workflow],
  );
  const nextPage = nextTarget(workflow, taskIntent, taskAutobox, isAd4Maps);
  const currentTaskRun = workflowRunForTask(workflow, taskIntent);
  const nextRunId =
    nextPage === "run-execute" || nextPage === "result"
      ? String(currentTaskRun?.run_id || "")
      : "";
  const artifacts = useMemo(
    () => {
      const preparedInputsReady = fileReady(workflow?.prepared?.receptor) && fileReady(workflow?.prepared?.ligand);
      const receptorRawReady = fileReady(workflow?.raw?.receptor);
      const ligandRawReady = fileReady(workflow?.raw?.ligand);
      return [
        artifact(
          "受体 raw",
          receptorRawReady
            ? fileState(workflow?.raw?.receptor)
            : preparedInputsReady
              ? "无需"
              : fileState(workflow?.raw?.receptor),
          workflow?.raw?.receptor?.path || (preparedInputsReady ? "直接使用 PDBQT" : "未记录"),
        ),
        artifact(
          "配体 raw",
          ligandRawReady
            ? fileState(workflow?.raw?.ligand)
            : preparedInputsReady
              ? "无需"
              : fileState(workflow?.raw?.ligand),
          workflow?.raw?.ligand?.path || (preparedInputsReady ? "直接使用 PDBQT" : "未记录"),
        ),
        artifact(
          "受体 PDBQT",
          fileState(workflow?.prepared?.receptor),
          workflow?.prepared?.receptor?.path || "未记录",
        ),
        artifact(
          "配体 PDBQT",
          fileState(workflow?.prepared?.ligand),
          workflow?.prepared?.ligand?.path || "未记录",
        ),
        artifact(
          "最近运行",
          runState(workflow, taskIntent),
          workflowRunForTask(workflow, taskIntent)?.run_id
            ? String(workflowRunForTask(workflow, taskIntent)?.run_id)
            : "当前任务尚未创建运行记录",
        ),
        artifact(
          taskCopy.resultArtifactLabel,
          String(workflowRunForTask(workflow, taskIntent)?.status ?? "") === "finished"
            ? "可进行"
            : "未开始",
          String(workflowRunForTask(workflow, taskIntent)?.status ?? "") === "finished"
            ? taskCopy.resultArtifactDetail
            : taskCopy.reportWaiting,
        ),
      ];
    },
    [
      taskCopy.reportWaiting,
      taskCopy.resultArtifactDetail,
      taskCopy.resultArtifactLabel,
      taskIntent,
      workflow,
    ],
  );

  if (!project) {
    const toolchainPending = !toolchainChecked;
    return (
      <PageShell className="first-run-landing" labelledBy="first-run-title">
        <PageHero
          title="新建分子任务"
          titleId="first-run-title"
          description="先选择文件来源；已有受体中的配体姿势也可直接评分或局部优化。"
          actions={
            <ActionButton variant="text" onClick={onOpenProject}>
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
                      <h3>已有 PDBQT（直接使用）</h3>
                      <p>导入受体和配体 PDBQT，跳过格式转换。</p>
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
                      <h3>PDB/CIF + SDF/MOL（准备并转换）</h3>
                      <p>在线搜索并下载，或导入本地原始结构，再转换为 PDBQT。</p>
                    </div>
                    <span className="secondary-button start-route-button start-route-button-proxy">选择结构来源</span>
                  </button>

                  <button className="start-route-card" data-layout="task-card" type="button" onClick={() => onNavigate("project-create", { startMode: "demo" })}>
                    <div className="start-route-card-copy">
                      <h3>示例项目（快速体验）</h3>
                      <p>使用内置示例完成一次对接。</p>
                    </div>
                    <span className="secondary-button start-route-button start-route-button-proxy">打开示例</span>
                  </button>
                </div>
              </section>

              <section className="dashboard-pose-task-entry" aria-labelledby="dashboard-pose-task-title">
                <div>
                  <span>已有受体中的配体姿势</span>
                  <h2 id="dashboard-pose-task-title">评价当前姿势</h2>
                  <p>受体与配体需处在同一坐标系；这些任务不会搜索新的结合位点。</p>
                </div>
                <div>
                  <button
                    type="button"
                    onClick={() => onNavigate("project-create", {
                      startMode: "basic",
                      taskIntent: "score_only",
                    })}
                  >
                    <strong>姿势评分</strong>
                    <span>计算当前姿势的能量项</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => onNavigate("project-create", {
                      startMode: "basic",
                      taskIntent: "local_only",
                    })}
                  >
                    <strong>局部优化</strong>
                    <span>从当前姿势附近优化</span>
                  </button>
                </div>
              </section>

              <p className="first-run-storage-note">
                DockStart 项目将保存受体、配体、任务类型、范围参数、运行日志和结果报告。
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
                  <span>选择“已有 PDBQT（直接使用）”。</span>
                </p>
                <p>
                  <strong>只有 PDB 或 SDF？</strong>
                  <span>选择“PDB/CIF + SDF/MOL（准备并转换）”。</span>
                </p>
                <p>
                  <strong>已经有放在受体中的配体姿势？</strong>
                  <span>使用姿势评分或局部优化入口。</span>
                </p>
              </div>
            </RightRailSection>
          </RightRail>
        </BodyGrid>
      </PageShell>
    );
  }

  const dashboardStepperIndex = projectStepperIndex(workflow, taskAutobox, taskIntent);

  return (
    <PageShell labelledBy="project-dashboard-title">
      <PageHero
        eyebrow="项目总览"
        title={project.project_name || "DockStart 项目"}
        titleId="project-dashboard-title"
        description={taskIntent === "dock"
          ? workflow?.next_recommended_action || taskCopy.heroDescription
          : taskCopy.heroDescription}
        actions={
          <>
          <ActionButton onClick={() => void loadWorkflow()}>{isBusy ? "刷新中..." : "刷新状态"}</ActionButton>
          <ActionButton onClick={() => onNavigate("project-create")}>创建项目</ActionButton>
          <ActionButton
            variant="primary"
            onClick={() => onNavigate(nextPage, { runId: nextRunId })}
          >
            继续当前步骤
          </ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <FilePathText value={project.project_dir} />

            <SectionCard title="本次任务">
              <div className="project-task-switch" role="group" aria-label="切换运行任务类型">
                {projectTaskOptions.map((option) => {
                  const active = taskIntent === option.id;
                  return (
                    <button
                      aria-pressed={active}
                      className={active ? "active" : ""}
                      disabled={Boolean(taskSwitching) || taskSwitchBlockedByActiveRun || active}
                      key={option.id}
                      onClick={() => void switchProjectTask(option.id)}
                      type="button"
                    >
                      <span>
                        <strong>{option.label}</strong>
                        <small>{option.description}</small>
                      </span>
                      <StatusBadge tone={active ? "ok" : "info"}>
                        {active ? "当前任务" : taskSwitching === option.id ? "切换中" : "切换"}
                      </StatusBadge>
                    </button>
                  );
                })}
              </div>
              {taskIntent !== "dock" ? (
                <p className="project-task-context-note">
                  受体与配体应处在同一坐标系；{taskIntentLabel(taskIntent)}不会搜索新的结合位点。
                </p>
              ) : null}
              {taskSwitchBlockedByActiveRun ? (
                <p className="project-task-context-note">
                  当前 Vina 运行尚未结束。请先打开运行工作台等待完成或安全取消，再切换任务类型。
                </p>
              ) : null}
            </SectionCard>

            <section className="dashboard-progress-strip" aria-label={taskCopy.stepperLabel}>
              <p className="first-run-stepper-status active">
                第 {dashboardStepperIndex + 1} 步 / 共 4 步：{taskCopy.stepperSteps[dashboardStepperIndex]}
              </p>
              <DockingStepper
                activeIndex={dashboardStepperIndex}
                ariaLabel={taskCopy.stepperLabel}
                labels={taskCopy.stepperSteps}
              />
            </section>

            <SectionCard title="工作流">
              <div className="dashboard-timeline">
                {rows.map((row) => (
                  <button
                    className="workflow-step action-card"
                    key={row.title}
                    type="button"
                    onClick={() => onNavigate(row.target, { runId: row.runId })}
                  >
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
                <dd>{taskCopy.stepperSteps[dashboardStepperIndex]}</dd>
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
            <p>切换任务后会进入运行工作台。姿势评分与局部优化只适用于单个配体。</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
