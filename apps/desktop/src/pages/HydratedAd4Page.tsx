import {
  ArrowClockwise,
  ChartBar,
  CheckCircle,
  Drop,
  GridFour,
  Play,
  ShieldCheck,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  hydratedApi,
  type HydratedDesktopApi,
} from "../api/hydrated";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import ErrorPanel from "../components/ErrorPanel";
import HydratedProtocolScope from "../components/HydratedProtocolScope";
import OperationLoadingDialog from "../components/OperationLoadingDialog";
import {
  BodyGrid,
  MainPanel,
  PageHero,
  PageShell,
  RightRail,
  RightRailSection,
} from "../components/layout/PageLayout";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type {
  DockStartProject,
  HydratedApiError,
  HydratedResultsSuccess,
  HydratedRunPreflightSuccess,
  HydratedStatusResponse,
  HydratedStatusSuccess,
} from "../types";
import {
  getHydratedWorkflowAvailability,
  hydratedRunSummaries,
  resolveHydratedRun,
  type HydratedRunSummary,
} from "../utils/hydratedWorkflow";

type HydratedAd4PageProps = {
  project: DockStartProject;
  currentRunId?: string;
  api?: HydratedDesktopApi;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenRunExecute: (project: DockStartProject, runId: string) => void;
};

type BusyAction =
  | ""
  | "refresh"
  | "prepare-ligand"
  | "generate-maps"
  | "preflight"
  | "prepare-run"
  | "load-results";

type WorkflowStepProps = {
  index: number;
  title: string;
  description: string;
  status: string;
  tone: "ok" | "warning" | "error" | "muted" | "info";
  detail?: ReactNode;
  actions?: ReactNode;
};

const runStatusLabels: Record<string, string> = {
  prepared: "已准备",
  queued: "等待执行",
  running: "运行中",
  finished: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  unknown: "需检查",
};

const busyDialogCopy: Record<
  Exclude<BusyAction, "" | "refresh">,
  { title: string; message: string; detail: string }
> = {
  "prepare-ligand": {
    title: "正在准备水合配体",
    message: "正在从当前 SDF 或 MOL 生成独立的水合 PDBQT。",
    detail: "原始配体和标准配体文件不会被覆盖。",
  },
  "generate-maps": {
    title: "正在生成水合 AD4 maps",
    message: "AutoGrid4 正在计算基础网格，并生成 W map。",
    detail: "所需时间取决于 Box 尺寸和本机性能。",
  },
  preflight: {
    title: "正在检查运行条件",
    message: "正在核对输入、maps、Vina 能力和文件哈希。",
    detail: "检查不会启动 Vina。",
  },
  "prepare-run": {
    title: "正在创建水合 run",
    message: "正在冻结配体、受体、maps、参数和工具版本。",
    detail: "完成后将进入现有 Vina 执行页。",
  },
  "load-results": {
    title: "正在读取水合结果",
    message: "正在核对 raw affinity 与逐构象水分子分类。",
    detail: "若评分表尚未生成，会先解析当前 run。",
  },
};

function WorkflowStep({
  index,
  title,
  description,
  status,
  tone,
  detail,
  actions,
}: WorkflowStepProps) {
  return (
    <article className="hydrated-workflow-step">
      <span aria-hidden="true" className="hydrated-step-index">
        {String(index).padStart(2, "0")}
      </span>
      <div className="hydrated-step-body">
        <div className="hydrated-step-heading">
          <div>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
          <StatusBadge tone={tone}>{status}</StatusBadge>
        </div>
        {detail ? <div className="hydrated-step-detail">{detail}</div> : null}
        {actions ? <div className="hydrated-step-actions">{actions}</div> : null}
      </div>
    </article>
  );
}

function fileName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function countText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : "—";
}

function runTone(
  run: HydratedRunSummary | null,
): "ok" | "warning" | "error" | "muted" | "info" {
  if (!run) return "muted";
  if (run.status === "finished" || run.status === "prepared") return "ok";
  if (run.status === "running" || run.status === "queued") return "info";
  if (run.status === "failed") return "error";
  return "warning";
}

function caughtError(error: unknown, fallbackTitle: string): HydratedApiError {
  return {
    code: "HYDRATED_REQUEST_FAILED",
    title: fallbackTitle,
    message: error instanceof Error ? error.message : String(error),
    suggestion: "请确认项目仍可访问，然后刷新状态后重试。",
  };
}

export default function HydratedAd4Page({
  project,
  currentRunId = "",
  api = hydratedApi,
  onBack,
  onProjectChange,
  onOpenRunExecute,
}: HydratedAd4PageProps) {
  const [status, setStatus] = useState<HydratedStatusSuccess | null>(null);
  const [preflight, setPreflight] =
    useState<HydratedRunPreflightSuccess | null>(null);
  const [results, setResults] = useState<HydratedResultsSuccess | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction>("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<HydratedApiError | null>(null);
  const initialRun = resolveHydratedRun(project, currentRunId);
  const [selectedRunId, setSelectedRunId] = useState(
    initialRun?.runId ?? "",
  );
  const mountedRef = useRef(true);
  const statusRequestRef = useRef(0);

  const runs = useMemo(() => hydratedRunSummaries(project), [project]);
  const selectedRun = useMemo(
    () => runs.find((run) => run.runId === selectedRunId) ?? null,
    [runs, selectedRunId],
  );
  const availability = getHydratedWorkflowAvailability({
    project,
    status,
    preflight,
    run: selectedRun,
    busy: Boolean(busyAction),
  });
  const rawLigand = project.ligand.raw_file;
  const ligandIssues = status?.issues ?? [];
  const mapsIssues = status?.maps_issues ?? [];
  const protocolIssues = [...ligandIssues, ...mapsIssues];
  const waterCount =
    status?.water_count
    ?? status?.manifest?.water_count
    ?? status?.manifest?.outputs?.hydrated_pdbqt?.water_count;
  const mapSetId = status?.maps_manifest?.map_set_id ?? "";

  useEffect(() => {
    const preferred = resolveHydratedRun(project, currentRunId);
    if (
      selectedRunId
      && runs.some((run) => run.runId === selectedRunId)
    ) {
      return;
    }
    setSelectedRunId(preferred?.runId ?? "");
  }, [currentRunId, project, runs, selectedRunId]);

  useEffect(() => {
    if (results && results.run_id !== selectedRunId) {
      setResults(null);
    }
  }, [results, selectedRunId]);

  const applyStatus = useCallback(
    (response: HydratedStatusResponse, fallbackTitle: string): boolean => {
      if (!mountedRef.current) return false;
      if (!response.ok) {
        setStatus(null);
        setError({
          ...response.error,
          title: response.error.title || fallbackTitle,
        });
        setNotice("");
        return false;
      }
      setStatus(response);
      setError(null);
      setNotice(response.message);
      onProjectChange(response.project);
      return true;
    },
    [onProjectChange],
  );

  const refreshStatus = useCallback(
    async (announce = true) => {
      const requestId = ++statusRequestRef.current;
      setBusyAction("refresh");
      if (announce) setNotice("正在刷新水合协议状态…");
      setError(null);
      try {
        const response = await api.getStatus(project.project_dir);
        if (
          !mountedRef.current
          || requestId !== statusRequestRef.current
        ) {
          return;
        }
        applyStatus(response, "无法读取水合协议状态");
        setPreflight(null);
      } catch (requestError) {
        if (
          mountedRef.current
          && requestId === statusRequestRef.current
        ) {
          setStatus(null);
          setError(caughtError(requestError, "无法读取水合协议状态"));
          setNotice("");
        }
      } finally {
        if (
          mountedRef.current
          && requestId === statusRequestRef.current
        ) {
          setBusyAction("");
        }
      }
    },
    [api, applyStatus, project.project_dir],
  );

  useEffect(() => {
    mountedRef.current = true;
    void refreshStatus(false);
    return () => {
      mountedRef.current = false;
      statusRequestRef.current += 1;
    };
  }, [refreshStatus]);

  const prepareLigand = async () => {
    if (!availability.canPrepareLigand) return;
    setBusyAction("prepare-ligand");
    setError(null);
    setNotice("");
    try {
      const response = await api.prepareLigand(project.project_dir);
      if (!mountedRef.current) return;
      if (applyStatus(response, "无法准备水合配体")) {
        setPreflight(null);
        setResults(null);
      }
    } catch (requestError) {
      if (mountedRef.current) {
        setError(caughtError(requestError, "无法准备水合配体"));
      }
    } finally {
      if (mountedRef.current) setBusyAction("");
    }
  };

  const generateMaps = async () => {
    if (!availability.canGenerateMaps) return;
    setBusyAction("generate-maps");
    setError(null);
    setNotice("");
    try {
      const response = await api.generateMaps(project.project_dir);
      if (!mountedRef.current) return;
      if (!response.ok) {
        setError({
          ...response.error,
          title: response.error.title || "无法生成水合 AD4 maps",
        });
        return;
      }
      onProjectChange(response.project);
      setNotice(response.message);
      setPreflight(null);
      setResults(null);
      const nextStatus = await api.getStatus(project.project_dir);
      if (mountedRef.current) {
        applyStatus(nextStatus, "无法核对新生成的水合 AD4 maps");
      }
    } catch (requestError) {
      if (mountedRef.current) {
        setError(caughtError(requestError, "无法生成水合 AD4 maps"));
      }
    } finally {
      if (mountedRef.current) setBusyAction("");
    }
  };

  const checkRun = async (): Promise<HydratedRunPreflightSuccess | null> => {
    if (!availability.canCheckRun) return null;
    setBusyAction("preflight");
    setError(null);
    setNotice("");
    try {
      const response = await api.getRunPreflight(project.project_dir);
      if (!mountedRef.current) return null;
      if (!response.ok) {
        setPreflight(null);
        setError({
          ...response.error,
          title: response.error.title || "水合运行前检查未通过",
        });
        return null;
      }
      setPreflight(response);
      setNotice(response.message);
      onProjectChange(response.project);
      return response;
    } catch (requestError) {
      if (mountedRef.current) {
        setPreflight(null);
        setError(caughtError(requestError, "无法完成水合运行前检查"));
      }
      return null;
    } finally {
      if (mountedRef.current) setBusyAction("");
    }
  };

  const prepareRun = async () => {
    if (!availability.canPrepareRun || !preflight) return;
    setBusyAction("prepare-run");
    setError(null);
    setNotice("");
    try {
      const verified = await api.getRunPreflight(project.project_dir);
      if (!mountedRef.current) return;
      const verifiedReady = (
        verified as (HydratedRunPreflightSuccess & { ready?: boolean })
      ).ready !== false;
      if (
        !verified.ok
        || !verifiedReady
        || verified.active_run_guard?.blocked === true
      ) {
        setPreflight(verified.ok ? verified : null);
        setError(
          verified.ok
            ? {
                code: "HYDRATED_ACTIVE_RUN_BLOCKED",
                title: "暂不能创建新的水合 run",
                message:
                  verified.active_run_guard?.message
                  || "运行条件已经变化，请重新处理阻塞项。",
                suggestion:
                  "请先处理当前未完成运行，再重新执行运行前检查。",
              }
            : {
                ...verified.error,
                title:
                  verified.error.title
                  || "水合运行前检查未通过",
              },
        );
        return;
      }
      setPreflight(verified);
      const response = await api.prepareRun(project.project_dir);
      if (!mountedRef.current) return;
      if (!response.ok) {
        setError({
          ...response.error,
          title: response.error.title || "无法创建水合 run",
        });
        return;
      }
      setSelectedRunId(response.run_id);
      setNotice(response.message);
      onProjectChange(response.project);
      onOpenRunExecute(response.project, response.run_id);
    } catch (requestError) {
      if (mountedRef.current) {
        setError(caughtError(requestError, "无法创建水合 run"));
      }
    } finally {
      if (mountedRef.current) setBusyAction("");
    }
  };

  const loadResults = async () => {
    if (!availability.canLoadResults || !selectedRun) return;
    setBusyAction("load-results");
    setError(null);
    setNotice("");
    try {
      const response = await api.loadResults(
        project.project_dir,
        selectedRun.runId,
      );
      if (!mountedRef.current) return;
      if (!response.ok) {
        setResults(null);
        setError({
          ...response.error,
          title: response.error.title || "无法读取水合结果",
        });
        return;
      }
      setResults(response);
      setNotice(response.message);
    } catch (requestError) {
      if (mountedRef.current) {
        setResults(null);
        setError(caughtError(requestError, "无法读取水合结果"));
      }
    } finally {
      if (mountedRef.current) setBusyAction("");
    }
  };

  const ligandStatus = status?.preparation_ready
    ? "已就绪"
    : status?.status === "invalid"
      ? "需重新准备"
      : "未准备";
  const mapsStatus = status?.maps_ready
    ? "已就绪"
    : status?.maps_status === "invalid"
      ? "需重新生成"
      : "未生成";
  const preflightReady = Boolean(
    preflight?.ok
    && preflight.active_run_guard?.blocked !== true
    && (
      preflight as HydratedRunPreflightSuccess & { ready?: boolean }
    ).ready !== false,
  );
  const loadingCopy =
    busyAction && busyAction !== "refresh"
      ? busyDialogCopy[busyAction]
      : null;

  return (
    <PageShell className="hydrated-ad4-page" labelledBy="hydrated-ad4-title">
      <PageHero
        eyebrow="实验协议 · Experimental"
        title="水合 AutoDock4 对接"
        titleId="hydrated-ad4-title"
        description="为单一配体准备显式水位点和 W map，在刚性受体上执行全局 AD4 对接。"
        actions={
          <>
            <StatusBadge
              tone={
                status?.preparation_ready && status.maps_ready
                  ? "ok"
                  : status
                    ? "warning"
                    : "muted"
              }
            >
              {status?.preparation_ready && status.maps_ready
                ? "输入与 maps 已就绪"
                : status
                  ? "尚未完成准备"
                  : "正在读取状态"}
            </StatusBadge>
            <ActionButton
              disabled={Boolean(busyAction)}
              onClick={() => void refreshStatus()}
            >
              {busyAction === "refresh" ? (
                <SpinnerGap
                  aria-hidden="true"
                  className="hydrated-spinner"
                  size={17}
                />
              ) : (
                <ArrowClockwise aria-hidden="true" size={17} />
              )}
              刷新
            </ActionButton>
            <ActionButton variant="text" onClick={onBack}>
              返回运行工作台
            </ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <HydratedProtocolScope />

            <section
              aria-labelledby="hydrated-overview-title"
              className="hydrated-overview"
            >
              <div className="hydrated-section-heading">
                <div>
                  <span>状态概览</span>
                  <h2 id="hydrated-overview-title">当前协议输入</h2>
                </div>
                <small>所有产物独立保存，不覆盖标准配体或 maps。</small>
              </div>
              <div className="hydrated-metrics">
                <article>
                  <span>原始配体</span>
                  <strong>{rawLigand ? fileName(rawLigand) : "未记录"}</strong>
                  <small>
                    {availability.rawLigandSupported
                      ? "SDF / MOL"
                      : "需要 SDF 或 MOL"}
                  </small>
                </article>
                <article>
                  <span>水合位点</span>
                  <strong>{countText(waterCount)}</strong>
                  <small>准备后记录的 W 原子</small>
                </article>
                <article>
                  <span>Maps</span>
                  <strong>{mapSetId || "未生成"}</strong>
                  <small>{status?.maps_ready ? "含 W.map" : "等待生成"}</small>
                </article>
                <article>
                  <span>当前 run</span>
                  <strong>{selectedRun?.runId || "未创建"}</strong>
                  <small>
                    {selectedRun
                      ? runStatusLabels[selectedRun.status]
                        || selectedRun.status
                      : "等待运行前检查"}
                  </small>
                </article>
              </div>
            </section>

            <section
              aria-label="水合 AD4 工作流"
              className="hydrated-workflow"
            >
              <WorkflowStep
                index={1}
                title="准备水合配体"
                description="从当前单配体的 SDF 或 MOL 生成独立水合 PDBQT，并记录输入与产物哈希。"
                status={ligandStatus}
                tone={
                  status?.preparation_ready
                    ? "ok"
                    : status?.status === "invalid"
                      ? "error"
                      : "warning"
                }
                detail={
                  status?.manifest ? (
                    <dl className="hydrated-inline-facts">
                      <div>
                        <dt>记录</dt>
                        <dd>
                          {status.active_ligand_manifest || "未记录"}
                        </dd>
                      </div>
                      <div>
                        <dt>水位点</dt>
                        <dd>{countText(waterCount)}</dd>
                      </div>
                    </dl>
                  ) : (
                    <p>
                      {availability.reasons.prepareLigand
                      || "已有记录仍可重新准备；新的有效记录会成为当前水合输入。"}
                    </p>
                  )
                }
                actions={
                  <ActionButton
                    disabled={!availability.canPrepareLigand}
                    onClick={() => void prepareLigand()}
                    title={availability.reasons.prepareLigand || undefined}
                  >
                    <Drop aria-hidden="true" size={17} weight="duotone" />
                    {status?.preparation_ready
                      ? "重新准备水合配体"
                      : "准备水合配体"}
                  </ActionButton>
                }
              />

              <WorkflowStep
                index={2}
                title="生成水合 AD4 maps"
                description="使用当前刚性受体、项目 Box 和水合配体原子类型生成完整 AD4 maps 与 W map。"
                status={mapsStatus}
                tone={
                  status?.maps_ready
                    ? "ok"
                    : status?.maps_status === "invalid"
                      ? "error"
                      : "warning"
                }
                detail={
                  status?.maps_manifest ? (
                    <dl className="hydrated-inline-facts">
                      <div>
                        <dt>Map set</dt>
                        <dd>{mapSetId || "未记录"}</dd>
                      </div>
                      <div>
                        <dt>Manifest</dt>
                        <dd>
                          {status.active_maps_manifest || "未记录"}
                        </dd>
                      </div>
                    </dl>
                  ) : (
                    <p>
                      {availability.reasons.generateMaps
                      || "AutoGrid4 必须已配置；生成过程会校验全部 map 文件。"}
                    </p>
                  )
                }
                actions={
                  <ActionButton
                    disabled={!availability.canGenerateMaps}
                    onClick={() => void generateMaps()}
                    title={availability.reasons.generateMaps || undefined}
                  >
                    <GridFour aria-hidden="true" size={17} />
                    {status?.maps_ready
                      ? "重新生成 maps"
                      : "生成并校验 maps"}
                  </ActionButton>
                }
              />

              <WorkflowStep
                index={3}
                title="运行前检查"
                description="核对水合配体、maps、Vina 1.2.x 能力、项目参数和活动运行守卫。"
                status={preflightReady ? "检查通过" : "待检查"}
                tone={preflightReady ? "ok" : "muted"}
                detail={
                  preflight ? (
                    <dl className="hydrated-inline-facts">
                      <div>
                        <dt>下一 run</dt>
                        <dd>{preflight.next_run_id}</dd>
                      </div>
                      <div>
                        <dt>Vina</dt>
                        <dd>{preflight.vina_binary.name || "已校验"}</dd>
                      </div>
                    </dl>
                  ) : (
                    <p>{availability.reasons.checkRun || "准备完成后执行检查。"}</p>
                  )
                }
                actions={
                  <ActionButton
                    disabled={!availability.canCheckRun}
                    onClick={() => void checkRun()}
                    title={availability.reasons.checkRun || undefined}
                  >
                    <ShieldCheck aria-hidden="true" size={17} />
                    运行前检查
                  </ActionButton>
                }
              />

              <WorkflowStep
                index={4}
                title="创建并执行 run"
                description="冻结当前协议输入后，进入现有 Vina 执行页启动、监控或取消任务。"
                status={
                  selectedRun
                    ? runStatusLabels[selectedRun.status]
                      || selectedRun.status
                    : preflightReady
                      ? "可以创建"
                      : "等待检查"
                }
                tone={selectedRun ? runTone(selectedRun) : preflightReady ? "ok" : "muted"}
                detail={
                  runs.length ? (
                    <label className="hydrated-run-selector">
                      <span>水合运行记录</span>
                      <select
                        disabled={Boolean(busyAction)}
                        onChange={(event) => {
                          setSelectedRunId(event.target.value);
                          setResults(null);
                          setError(null);
                        }}
                        value={selectedRunId}
                      >
                        {runs.map((run) => (
                          <option key={run.runId} value={run.runId}>
                            {run.runId} ·{" "}
                            {runStatusLabels[run.status] || run.status}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <p>{availability.reasons.prepareRun || "尚未创建水合 run。"}</p>
                  )
                }
                actions={
                  <>
                    <ActionButton
                      disabled={!availability.canPrepareRun}
                      onClick={() => void prepareRun()}
                      title={availability.reasons.prepareRun || undefined}
                      variant="primary"
                    >
                      <Play aria-hidden="true" size={17} weight="fill" />
                      创建 run 并进入执行页
                    </ActionButton>
                    {selectedRun ? (
                      <ActionButton
                        disabled={Boolean(busyAction)}
                        onClick={() =>
                          onOpenRunExecute(project, selectedRun.runId)
                        }
                      >
                        打开 {selectedRun.runId}
                      </ActionButton>
                    ) : null}
                  </>
                }
              />

              <WorkflowStep
                index={5}
                title="读取水合结果"
                description="在 run 完成后核对 raw AD4 affinity、RMSD 与每个构象的保留、强、弱和置换水记录。"
                status={
                  results
                    ? "已加载"
                    : selectedRun?.status === "finished"
                      ? "可以读取"
                      : "等待运行完成"
                }
                tone={
                  results
                    ? "ok"
                    : selectedRun?.status === "finished"
                      ? "info"
                      : "muted"
                }
                detail={
                  <p>
                    {availability.reasons.loadResults
                    || "评分保持为 raw hydrated AD4 affinity；水分子后处理不生成新评分。"}
                  </p>
                }
                actions={
                  <ActionButton
                    disabled={!availability.canLoadResults}
                    onClick={() => void loadResults()}
                    title={availability.reasons.loadResults || undefined}
                  >
                    <ChartBar aria-hidden="true" size={17} />
                    加载水合结果
                  </ActionButton>
                }
              />
            </section>

            {preflight?.warnings?.length ? (
              <WarningCallout title="运行前提示">
                <ul className="hydrated-message-list">
                  {preflight.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </WarningCallout>
            ) : null}

            {results ? (
              <section
                aria-labelledby="hydrated-results-title"
                className="hydrated-results"
              >
                <div className="hydrated-section-heading">
                  <div>
                    <span>{results.run_id}</span>
                    <h2 id="hydrated-results-title">水分子分类结果</h2>
                  </div>
                  <StatusBadge tone="warning">
                    处理后评分未计算
                  </StatusBadge>
                </div>
                <div className="hydrated-water-summary">
                  <article>
                    <span>原始候选水</span>
                    <strong>
                      {countText(results.water_summary.raw_water_count)}
                    </strong>
                  </article>
                  <article>
                    <span>保留水</span>
                    <strong>
                      {countText(results.water_summary.retained_water_count)}
                    </strong>
                  </article>
                  <article>
                    <span>强水</span>
                    <strong>
                      {countText(results.water_summary.strong_water_count)}
                    </strong>
                  </article>
                  <article>
                    <span>弱水</span>
                    <strong>
                      {countText(results.water_summary.weak_water_count)}
                    </strong>
                  </article>
                  <article>
                    <span>置换水</span>
                    <strong>
                      {countText(results.water_summary.displaced_water_count)}
                    </strong>
                  </article>
                </div>
                <div className="hydrated-results-table-wrap">
                  <table className="hydrated-results-table">
                    <thead>
                      <tr>
                        <th scope="col">Mode</th>
                        <th scope="col">Raw AD4 affinity</th>
                        <th scope="col">RMSD lower</th>
                        <th scope="col">强水</th>
                        <th scope="col">弱水</th>
                        <th scope="col">置换水</th>
                      </tr>
                    </thead>
                    <tbody>
                      {results.modes.map((mode) => (
                        <tr key={mode.mode}>
                          <th scope="row">{mode.mode}</th>
                          <td>{mode.raw_affinity_kcal_mol} kcal/mol</td>
                          <td>{mode.rmsd_lb} Å</td>
                          <td>
                            {countText(
                              mode.water_summary.strong_water_count,
                            )}
                          </td>
                          <td>
                            {countText(
                              mode.water_summary.weak_water_count,
                            )}
                          </td>
                          <td>
                            {countText(
                              mode.water_summary.displaced_water_count,
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <AdvancedDetails summary="结果文件">
                  <dl className="hydrated-result-files">
                    <div>
                      <dt>Raw 输出</dt>
                      <dd>{results.raw_output_file}</dd>
                    </div>
                    <div>
                      <dt>保留水输出</dt>
                      <dd>{results.retained_output_file}</dd>
                    </div>
                    <div>
                      <dt>去水配体输出</dt>
                      <dd>{results.water_free_output_file}</dd>
                    </div>
                  </dl>
                </AdvancedDetails>
              </section>
            ) : null}

            {error ? <ErrorPanel error={error} /> : null}
            {notice ? (
              <p
                className="hydrated-inline-notice"
                role="status"
              >
                {notice}
              </p>
            ) : null}
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="流程门禁">
            <ol className="hydrated-gate-list">
              <li className={status?.preparation_ready ? "ready" : ""}>
                {status?.preparation_ready ? (
                  <CheckCircle aria-hidden="true" weight="fill" />
                ) : (
                  <WarningCircle aria-hidden="true" weight="fill" />
                )}
                水合配体
              </li>
              <li className={status?.maps_ready ? "ready" : ""}>
                {status?.maps_ready ? (
                  <CheckCircle aria-hidden="true" weight="fill" />
                ) : (
                  <WarningCircle aria-hidden="true" weight="fill" />
                )}
                AD4 maps 与 W.map
              </li>
              <li className={preflightReady ? "ready" : ""}>
                {preflightReady ? (
                  <CheckCircle aria-hidden="true" weight="fill" />
                ) : (
                  <WarningCircle aria-hidden="true" weight="fill" />
                )}
                运行前检查
              </li>
              <li className={selectedRun?.status === "finished" ? "ready" : ""}>
                {selectedRun?.status === "finished" ? (
                  <CheckCircle aria-hidden="true" weight="fill" />
                ) : (
                  <WarningCircle aria-hidden="true" weight="fill" />
                )}
                完成水合 run
              </li>
            </ol>
          </RightRailSection>

          <RightRailSection title="当前绑定">
            <dl className="hydrated-context-list">
              <div>
                <dt>受体</dt>
                <dd>{project.receptor.file || "未准备"}</dd>
              </div>
              <div>
                <dt>原始配体</dt>
                <dd>{rawLigand || "未记录"}</dd>
              </div>
              <div>
                <dt>Box 中心</dt>
                <dd>
                  {project.box.center_x}, {project.box.center_y},{" "}
                  {project.box.center_z} Å
                </dd>
              </div>
              <div>
                <dt>Box 尺寸</dt>
                <dd>
                  {project.box.size_x} × {project.box.size_y} ×{" "}
                  {project.box.size_z} Å
                </dd>
              </div>
            </dl>
          </RightRailSection>

          {protocolIssues.length ? (
            <RightRailSection title="需要处理">
              <ul className="hydrated-issue-list">
                {protocolIssues.slice(0, 5).map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
              {protocolIssues.length > 5 ? (
                <small>另有 {protocolIssues.length - 5} 项，请刷新后复核。</small>
              ) : null}
            </RightRailSection>
          ) : null}

          <RightRailSection title="评分说明">
            <p>
              affinity 是含显式 W 原子的原始 AD4 评分，只用于同一 run
              内构象排序。水分子过滤不会替换该分值。
            </p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>

      <OperationLoadingDialog
        detail={loadingCopy?.detail}
        message={loadingCopy?.message ?? ""}
        open={Boolean(loadingCopy)}
        title={loadingCopy?.title ?? ""}
      />
    </PageShell>
  );
}
