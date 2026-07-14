import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { CheckCircle, FileArrowUp, FolderOpen, Info, Wrench } from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import { BodyGrid, MainPanel, ModeTabs, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import StatusBadge from "../components/StatusBadge";
import type {
  DockStartProject,
  PreparationResult,
  PreparationStatusResponse,
  PreparationTarget,
  PreparationToolCapabilityResult,
  RunFileStatus,
  ToolCheckResult,
} from "../types";
import {
  cancelQueuedBackgroundTask,
  findActiveBackgroundTask,
  startPreparationTask,
  waitForBackgroundTask,
  type BackgroundTaskStatus,
} from "../utils/backgroundTasks";

const StructureMiniPreview = lazy(() => import("../components/StructureMiniPreview"));

type PreparationMode = "existing" | "raw";

type PreparationPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenImportPdbqt: (project: DockStartProject) => void;
  onOpenBoxSetup: (project: DockStartProject) => void;
  onProjectChange: (project: DockStartProject) => void;
};

function parsePreparationResponse(rawPayload: string): PreparationStatusResponse {
  const parsed = JSON.parse(rawPayload) as Partial<PreparationStatusResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir ?? "",
    project: parsed.project ?? null,
    preparation: parsed.preparation ?? null,
    tools: parsed.tools,
    files: parsed.files,
    target: parsed.target,
    ready: parsed.ready,
    missing_tools: parsed.missing_tools ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function statusLabel(status: string | undefined): string {
  const labels: Record<string, string> = {
    ok: "可用",
    missing: "缺失",
    error: "失败",
    unknown: "需检查",
    not_started: "未开始",
    checking: "进行中",
    ready: "可进行",
    running: "进行中",
    finished: "已完成",
    failed: "失败",
    empty: "需检查",
  };
  return labels[status ?? ""] ?? "需检查";
}

function statusTone(status: string | undefined): "ok" | "warning" | "error" | "muted" | "info" {
  if (status === "ok" || status === "ready" || status === "finished") return "ok";
  if (status === "running" || status === "checking") return "info";
  if (status === "failed" || status === "error") return "error";
  if (status === "missing" || status === "empty") return "warning";
  return "muted";
}

function fileLine(file: RunFileStatus | undefined, fallback: string): string {
  if (!file) return fallback || "未记录";
  return file.path || fallback || "未记录";
}

function toolVersion(tool: ToolCheckResult | PreparationToolCapabilityResult | undefined): string {
  return tool?.version || "未获取版本";
}

function capabilityLine(tool: PreparationToolCapabilityResult | undefined, capabilityKey: string): string {
  const capability = tool?.capabilities?.[capabilityKey];
  if (!capability) return "未检测";
  return `${statusLabel(capability.status)} · ${capability.message}`;
}

export default function PreparationPage({
  project: initialProject,
  onBack,
  onOpenImportPdbqt,
  onOpenBoxSetup,
  onProjectChange,
}: PreparationPageProps) {
  const [project, setProject] = useState(initialProject);
  const [response, setResponse] = useState<PreparationStatusResponse | null>(null);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [overwriteReceptor, setOverwriteReceptor] = useState(false);
  const [overwriteLigand, setOverwriteLigand] = useState(false);
  const [mode, setMode] = useState<PreparationMode>(
    initialProject.receptor.raw_file
      || initialProject.ligand.raw_file
      || !(initialProject.receptor.file && initialProject.ligand.file)
      ? "raw"
      : "existing",
  );
  const [previewRevision, setPreviewRevision] = useState<Record<PreparationTarget, number>>({ receptor: 0, ligand: 0 });
  const [previewRequested, setPreviewRequested] = useState<Record<PreparationTarget, boolean>>({ receptor: false, ligand: false });
  const [tools, setTools] = useState<PreparationStatusResponse["tools"]>();
  const [isCheckingTools, setIsCheckingTools] = useState(false);
  const [pendingTarget, setPendingTarget] = useState<PreparationTarget | null>(null);
  const [activeTask, setActiveTask] = useState<BackgroundTaskStatus | null>(null);
  const activeTaskAbortRef = useRef<AbortController | null>(null);
  const preparedIdentityRef = useRef(`${initialProject.receptor.file}|${initialProject.ligand.file}`);

  useEffect(() => {
    const nextIdentity = `${initialProject.receptor.file}|${initialProject.ligand.file}`;
    if (preparedIdentityRef.current !== nextIdentity) {
      const [previousReceptor, previousLigand] = preparedIdentityRef.current.split("|");
      preparedIdentityRef.current = nextIdentity;
      setPreviewRevision((revision) => ({
        receptor: previousReceptor !== initialProject.receptor.file ? revision.receptor + 1 : revision.receptor,
        ligand: previousLigand !== initialProject.ligand.file ? revision.ligand + 1 : revision.ligand,
      }));
    }
    setProject(initialProject);
  }, [initialProject]);

  useEffect(() => () => activeTaskAbortRef.current?.abort(), []);

  const applyResponse = useCallback(
    (next: PreparationStatusResponse, fallbackMessage: string, completedTarget?: PreparationTarget) => {
      setResponse(next);
      if (next.tools) setTools(next.tools);
      if (next.project) {
        const nextIdentity = `${next.project.receptor.file}|${next.project.ligand.file}`;
        if (completedTarget || preparedIdentityRef.current !== nextIdentity) {
          const [previousReceptor, previousLigand] = preparedIdentityRef.current.split("|");
          preparedIdentityRef.current = nextIdentity;
          setPreviewRevision((revision) => ({
            receptor:
              completedTarget === "receptor" || previousReceptor !== next.project?.receptor.file
                ? revision.receptor + 1
                : revision.receptor,
            ligand:
              completedTarget === "ligand" || previousLigand !== next.project?.ligand.file
                ? revision.ligand + 1
                : revision.ligand,
          }));
        }
        setProject(next.project);
        onProjectChange(next.project);
      }
      setMessage(next.message ?? next.error?.message ?? fallbackMessage);
      setRawError(next.error?.raw_error ?? "");
    },
    [onProjectChange],
  );

  const waitForPreparation = useCallback(
    async (started: BackgroundTaskStatus, target: PreparationTarget, controller: AbortController) => {
      setActiveTask(started);
      const completed = await waitForBackgroundTask(
        started.task_id,
        (task) => {
          setActiveTask(task);
          setMessage(task.progress.message || task.message);
          if (task.error) setRawError(task.error);
        },
        controller.signal,
      );
      setActiveTask(completed);
      if (completed.status === "cancelled") {
        setMessage("排队中的结构准备任务已取消。");
        return;
      }
      if (!completed.result_json) {
        throw new Error(completed.error || completed.message || "结构准备后台任务没有返回结果。");
      }
      const preparationResult = parsePreparationResponse(completed.result_json);
      applyResponse(
        preparationResult,
        target === "receptor" ? "受体输入已准备。" : "配体输入已准备。",
        preparationResult.ok ? target : undefined,
      );
    },
    [applyResponse],
  );

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let resumedTaskId = "";
    const reconnect = async () => {
      try {
        const existing = await findActiveBackgroundTask(initialProject.project_dir, { kind: "preparation" });
        if (!existing || disposed) return;
        resumedTaskId = existing.task_id;
        activeTaskAbortRef.current?.abort();
        activeTaskAbortRef.current = controller;
        setIsBusy(true);
        setMessage("检测到未完成的结构准备任务，正在恢复进度显示。");
        await waitForPreparation(existing, existing.target === "receptor" ? "receptor" : "ligand", controller);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!disposed) {
          setMessage("无法恢复结构准备任务状态。");
          setRawError(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!disposed) {
          setActiveTask((current) => (current?.task_id === resumedTaskId ? null : current));
          setIsBusy(false);
        }
        if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      }
    };
    void reconnect();
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [initialProject.project_dir, waitForPreparation]);

  const reloadStatus = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_preparation_status", {
        projectDir: project.project_dir,
      });
      const parsed = parsePreparationResponse(rawPayload);
      applyResponse(parsed, "准备状态已刷新。");
    } catch (error) {
      setMessage("无法读取准备状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyResponse, project.project_dir]);

  useEffect(() => {
    void reloadStatus();
  }, [reloadStatus]);

  const checkConversionTools = async () => {
    setIsCheckingTools(true);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("get_preparation_tool_status", {
        projectDir: project.project_dir,
      });
      const parsed = parsePreparationResponse(rawPayload);
      if (!parsed.ok) {
        setMessage(parsed.error?.message ?? "无法检测格式转换工具。");
        setRawError(parsed.error?.raw_error ?? "");
        return;
      }
      setTools(parsed.tools);
      setMessage("格式转换工具检测完成；开始转换时仍会复核输入和输出。" );
    } catch (error) {
      setMessage("无法检测格式转换工具。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsCheckingTools(false);
    }
  };

  const resetTarget = async (target: PreparationTarget) => {
    const label = target === "receptor" ? "受体" : "配体";
    if (!window.confirm(`确定重置${label}准备状态吗？已有 Vina 输入文件不会被删除。`)) return;
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("reset_preparation_status", {
        projectDir: project.project_dir,
        target,
      });
      applyResponse(parsePreparationResponse(rawPayload), `${label}准备状态已重置。`);
    } catch (error) {
      setMessage("无法重置准备状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const prepareTarget = async (target: PreparationTarget) => {
    setPendingTarget(target);
    setIsBusy(true);
    setRawError("");
    activeTaskAbortRef.current?.abort();
    const controller = new AbortController();
    let taskId = "";
    activeTaskAbortRef.current = controller;
    try {
      const started = await startPreparationTask(
        project.project_dir,
        target,
        target === "receptor" ? overwriteReceptor : overwriteLigand,
      );
      taskId = started.task_id;
      setMessage(started.deduplicated ? "同一准备任务已在运行，正在接收其进度。" : "准备任务已进入后台队列。界面可以继续响应。" );
      await waitForPreparation(started, target, controller);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage(target === "receptor" ? "无法准备受体输入。" : "无法准备配体输入。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (activeTaskAbortRef.current === controller) activeTaskAbortRef.current = null;
      setActiveTask((current) => (current?.task_id === taskId ? null : current));
      setPendingTarget(null);
      setIsBusy(false);
    }
  };

  const cancelQueuedPreparation = async () => {
    if (!activeTask || activeTask.status !== "queued") return;
    try {
      const cancelled = await cancelQueuedBackgroundTask(activeTask.task_id);
      setActiveTask(cancelled);
      setMessage(
        cancelled.status === "cancelled"
          ? (cancelled.message || "排队中的结构准备任务已取消。")
          : "任务已经开始执行，不能再从队列取消。",
      );
    } catch (error) {
      setMessage("无法取消排队中的结构准备任务。");
      setRawError(error instanceof Error ? error.message : String(error));
    }
  };

  const loadLog = async (target: PreparationTarget) => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>(
        target === "receptor" ? "load_receptor_preparation_log" : "load_ligand_preparation_log",
        { projectDir: project.project_dir },
      );
      const parsed = JSON.parse(rawPayload) as {
        message?: string;
        stderr?: string;
        stdout?: string;
        log?: string;
        error?: { message: string; raw_error: string };
      };
      setMessage(parsed.message ?? parsed.error?.message ?? "准备日志已读取。");
      setRawError([parsed.stderr, parsed.stdout, parsed.log, parsed.error?.raw_error].filter(Boolean).join("\n\n"));
    } catch (error) {
      setMessage("无法读取准备日志。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const preparation = response?.preparation ?? project.preparation;
  const receptorPrep: PreparationResult | undefined = preparation?.receptor;
  const ligandPrep: PreparationResult | undefined = preparation?.ligand;
  const files = response?.files;
  const readyForBox = files?.receptor_prepared?.status === "ok" && files?.ligand_prepared?.status === "ok";
  const interactionBusy = Boolean(pendingTarget || activeTask?.status === "queued" || activeTask?.status === "running");

  const renderStructureRow = (target: PreparationTarget, prep: PreparationResult | undefined) => {
    const isReceptor = target === "receptor";
    const label = isReceptor ? "受体" : "配体";
    const rawFile = isReceptor ? files?.receptor_raw : files?.ligand_raw;
    const preparedFile = isReceptor ? files?.receptor_prepared : files?.ligand_prepared;
    const projectFile = isReceptor ? project.receptor.file : project.ligand.file;
    const projectRawFile = isReceptor ? project.receptor.raw_file : project.ligand.raw_file;
    const rawReady = rawFile?.status === "ok";
    const isReady = preparedFile?.status === "ok";
    const displayFile = isReady
      ? fileLine(preparedFile, projectFile)
      : mode === "raw"
        ? fileLine(rawFile, projectRawFile)
        : fileLine(preparedFile, projectFile);
    const fileName = displayFile.split(/[\\/]/).filter(Boolean).pop() || "尚未选择 PDBQT";
    const preparedSize = preparedFile?.size ?? 0;
    const shouldLoadPreview = isReady && previewRequested[target];

    return (
      <article className="preparation-target-row">
        <div className="preparation-target-identity">
          <span className="preparation-target-icon"><FileArrowUp aria-hidden="true" size={24} /></span>
          <div>
            <span>{label}</span>
            <strong>{fileName}</strong>
            <small>{isReady ? "PDBQT 已就绪" : rawReady ? (isReceptor ? "PDB / CIF" : "SDF / MOL") : "等待文件"}</small>
          </div>
        </div>

        {shouldLoadPreview ? (
          <Suspense fallback={<div className="structure-mini-preview structure-mini-preview-loading">正在加载 3D 预览…</div>}>
            <StructureMiniPreview
              fileKind={isReceptor ? "receptor_prepared" : "ligand_prepared"}
              label={label}
              projectDir={project.project_dir}
              refreshKey={previewRevision[target]}
            />
          </Suspense>
        ) : (
          <div className="structure-mini-preview structure-mini-preview-gate">
            {isReady ? (
              <>
                <strong>3D 预览按需加载</strong>
                <span>
                  {preparedSize > 0
                    ? `文件大小 ${preparedSize.toLocaleString()} B；点击后再初始化查看器，避免进入页面时卡顿。`
                    : "点击后再初始化查看器，避免进入页面时卡顿。"}
                </span>
                <ActionButton onClick={() => setPreviewRequested((current) => ({ ...current, [target]: true }))}>
                  加载 3D 预览
                </ActionButton>
              </>
            ) : (
              <>
                <strong>{mode === "raw" ? "等待转换为 PDBQT" : "等待导入 PDBQT"}</strong>
                <span>文件就绪后才会初始化 3D 查看器。</span>
              </>
            )}
          </div>
        )}

        <div className="preparation-target-actions">
          <div className={`preparation-file-check ${isReady ? "is-ready" : "is-missing"}`}>
            {isReady ? <CheckCircle aria-hidden="true" size={18} weight="fill" /> : <Info aria-hidden="true" size={18} weight="fill" />}
            <div>
              <strong>{isReady ? "PDBQT 已就绪" : rawReady ? "可以开始转换" : "等待原始文件"}</strong>
              <span>{preparedFile?.size ? `${preparedFile.size.toLocaleString()} B` : statusLabel(prep?.status)}</span>
            </div>
          </div>

          {mode === "existing" ? (
            <>
              <ActionButton variant="primary" disabled={interactionBusy} onClick={() => onOpenImportPdbqt(project)}>
                <FolderOpen aria-hidden="true" size={16} /> 选择文件
              </ActionButton>
              <ActionButton disabled={interactionBusy || isBusy} onClick={() => void reloadStatus()}>刷新文件状态</ActionButton>
            </>
          ) : (
            <>
              <label className="checkbox-row compact">
                <input
                  type="checkbox"
                  checked={isReceptor ? overwriteReceptor : overwriteLigand}
                  onChange={(event) => (isReceptor ? setOverwriteReceptor(event.target.checked) : setOverwriteLigand(event.target.checked))}
                />
                覆盖已有 PDBQT
              </label>
              <ActionButton variant="primary" disabled={interactionBusy || !rawReady} onClick={() => void prepareTarget(target)}>
                {isReceptor ? "转换受体为 PDBQT" : "转换配体为 PDBQT"}
              </ActionButton>
            </>
          )}

          <AdvancedDetails className="preparation-target-details" summary="查看详情">
            <dl className="meta-list">
              <div><dt>原始输入</dt><dd><code>{fileLine(rawFile, projectRawFile)}</code></dd></div>
              <div><dt>准备方法</dt><dd>{prep?.method ?? "外部或手动导入"}</dd></div>
              <div><dt>日志</dt><dd><code>{prep?.log_file || "未生成"}</code></dd></div>
            </dl>
            <div className="button-row">
              <ActionButton variant="text" onClick={() => void loadLog(target)} disabled={interactionBusy}>读取日志</ActionButton>
              <ActionButton variant="text" onClick={() => void resetTarget(target)} disabled={interactionBusy}>重置状态</ActionButton>
            </div>
          </AdvancedDetails>
        </div>
      </article>
    );
  };

  return (
    <PageShell labelledBy="preparation-title" className="preparation-workspace-page">
      <PageHero
        eyebrow="格式转换 · PDBQT PREPARATION"
        title="格式转换与 PDBQT 准备"
        titleId="preparation-title"
        description="将受体 PDB/CIF 与配体 SDF/MOL 准备并转换为 PDBQT，或直接导入已有 PDBQT。"
        actions={(
          <>
            <ActionButton variant="primary" onClick={onBack}>在线搜索并下载</ActionButton>
            <ActionButton onClick={() => onOpenImportPdbqt(project)}>导入已有 PDBQT</ActionButton>
            {activeTask?.status === "queued" ? (
              <ActionButton onClick={() => void cancelQueuedPreparation()}>取消排队</ActionButton>
            ) : null}
            <ActionButton onClick={() => void reloadStatus()} disabled={isBusy}>{isBusy ? "刷新中…" : "刷新状态"}</ActionButton>
          </>
        )}
      />

      <ModeTabs
        id="preparation-mode-tabs"
        label="PDBQT 输入方式"
        active={mode}
        onChange={setMode}
        options={[
          { id: "existing", label: "直接导入已有 PDBQT" },
          { id: "raw", label: "PDB/CIF + SDF/MOL（准备并转换）" },
        ]}
      />

      <BodyGrid className="preparation-workspace-layout">
        <MainPanel className="preparation-stage-panel">
          <div className="preparation-target-list">
            {renderStructureRow("receptor", receptorPrep)}
            {renderStructureRow("ligand", ligandPrep)}
          </div>

          {mode === "raw" ? (
            <div className="preparation-source-strip">
              <div>
                <Wrench aria-hidden="true" size={18} />
                <span>还没有原始文件？可在线搜索，也可在获取页从电脑导入。转换会自动检查 Python、RDKit 与 Meeko。</span>
              </div>
              <ActionButton variant="primary" onClick={onBack}>获取或导入原始结构</ActionButton>
            </div>
          ) : (
            <button className="preparation-drop-zone" type="button" onClick={() => onOpenImportPdbqt(project)}>
              <FileArrowUp aria-hidden="true" size={20} />
              <span>选择受体与配体 PDBQT；导入后会复制到当前项目。</span>
            </button>
          )}

          <div className="preparation-feedback">
            <ScientificDisclaimer kind="preparation" />
            {message || rawError ? <CommandResultPanel title="格式转换状态" message={message} rawError={rawError} /> : null}
          </div>

          <footer className="preparation-action-bar">
            <p>自动准备结果仍需人工检查质子化、电荷、构象和缺失残基。</p>
            <div>
              <ActionButton onClick={() => void reloadStatus()} disabled={isBusy}>{isBusy ? "刷新中…" : "刷新文件状态"}</ActionButton>
              <ActionButton variant="primary" disabled={!readyForBox} onClick={() => onOpenBoxSetup(project)}>
                PDBQT 已就绪，设置搜索范围
              </ActionButton>
            </div>
          </footer>
        </MainPanel>

        <RightRail className="preparation-context-rail">
          <RightRailSection title="当前输入">
            <dl className="mode-context-list">
              <div><dt>受体</dt><dd>{mode === "raw" ? files?.receptor_raw?.path || project.receptor.raw_file || "未选择" : files?.receptor_prepared?.path || "未选择"}</dd></div>
              <div><dt>配体</dt><dd>{mode === "raw" ? files?.ligand_raw?.path || project.ligand.raw_file || "未选择" : files?.ligand_prepared?.path || "未选择"}</dd></div>
            </dl>
          </RightRailSection>

          <RightRailSection title="文件检查">
            <div className="preparation-check-list">
              <span className={files?.receptor_prepared?.status === "ok" ? "ready" : "missing"}><CheckCircle aria-hidden="true" size={16} weight="fill" /> 受体 PDBQT</span>
              <span className={files?.ligand_prepared?.status === "ok" ? "ready" : "missing"}><CheckCircle aria-hidden="true" size={16} weight="fill" /> 配体 PDBQT</span>
            </div>
          </RightRailSection>

          <RightRailSection title="工具状态">
            {mode === "existing" ? (
              <p>直接导入 PDBQT 不需要 RDKit / Meeko。</p>
            ) : (
              <>
                <p className="preparation-profile-hint">
                  Assisted Stable 随附 RDKit / Meeko；Basic Stable 默认直接导入 PDBQT，也可使用已配置的兼容 Python 工具链。
                </p>
                <p>{tools ? "已读取本次工具检测结果。" : "进入页面时不再自动启动检测脚本；开始转换时会自动检查。"}</p>
                <ActionButton disabled={isCheckingTools || interactionBusy} onClick={() => void checkConversionTools()}>
                  {isCheckingTools ? "检测中…" : "检查转换工具"}
                </ActionButton>
                {tools ? (
                  <AdvancedDetails summary="查看检测详情">
                    <dl className="mode-context-list">
                      <div><dt>Python</dt><dd>{statusLabel(tools.python?.status)} · {toolVersion(tools.python)}</dd></div>
                      <div><dt>RDKit</dt><dd>{statusLabel(tools.rdkit?.status)} · {capabilityLine(tools.rdkit, "sdf_inline_read")}</dd></div>
                      <div><dt>Meeko</dt><dd>{statusLabel(tools.meeko?.status)}</dd></div>
                    </dl>
                  </AdvancedDetails>
                ) : null}
              </>
            )}
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>{readyForBox ? "设置对接搜索范围，然后在同一工作台复核 Vina 参数。" : "先补全受体与配体 PDBQT。"}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
