import { lazy, Suspense, useCallback, useEffect, useState } from "react";
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
  PreparationToolStatusResponse,
  RunFileStatus,
  ToolCheckResult,
} from "../types";

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

function parsePreparationToolStatusResponse(rawPayload: string): PreparationToolStatusResponse {
  return JSON.parse(rawPayload) as PreparationToolStatusResponse;
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
    initialProject.receptor.file && initialProject.ligand.file ? "existing" : "raw",
  );
  const [previewRevision, setPreviewRevision] = useState(0);

  useEffect(() => {
    setProject(initialProject);
  }, [initialProject]);

  const applyResponse = useCallback(
    (next: PreparationStatusResponse, fallbackMessage: string) => {
      setResponse(next);
      if (next.project) {
        setProject(next.project);
        onProjectChange(next.project);
        setPreviewRevision((revision) => revision + 1);
      }
      setMessage(next.message ?? next.error?.message ?? fallbackMessage);
      setRawError(next.error?.raw_error ?? "");
    },
    [onProjectChange],
  );

  const reloadStatus = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_preparation_status", {
        projectDir: project.project_dir,
      });
      const parsed = parsePreparationResponse(rawPayload);
      try {
        const rawToolPayload = await invoke<string>("get_preparation_tool_status", {
          projectDir: project.project_dir,
        });
        const toolStatus = parsePreparationToolStatusResponse(rawToolPayload);
        parsed.tools = toolStatus.tools ?? parsed.tools;
      } catch {
        // Keep best-effort preparation status.
      }
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

  const validateTarget = async (target: PreparationTarget) => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("validate_preparation_prerequisites", {
        projectDir: project.project_dir,
        target,
      });
      applyResponse(parsePreparationResponse(rawPayload), "准备条件已检查。");
    } catch (error) {
      setMessage("无法检查准备条件。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
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
    setIsBusy(true);
    setRawError("");
    try {
      const rawPayload = await invoke<string>(target === "receptor" ? "prepare_receptor_pdbqt" : "prepare_ligand_pdbqt", {
        projectDir: project.project_dir,
        overwrite: target === "receptor" ? overwriteReceptor : overwriteLigand,
      });
      applyResponse(parsePreparationResponse(rawPayload), target === "receptor" ? "受体输入已准备。" : "配体输入已准备。");
    } catch (error) {
      setMessage(target === "receptor" ? "无法准备受体输入。" : "无法准备配体输入。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
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
  const tools = response?.tools;
  const readyForBox = Boolean(project.receptor.file && project.ligand.file);

  const renderStructureRow = (target: PreparationTarget, prep: PreparationResult | undefined) => {
    const isReceptor = target === "receptor";
    const label = isReceptor ? "受体" : "配体";
    const rawFile = isReceptor ? files?.receptor_raw : files?.ligand_raw;
    const preparedFile = isReceptor ? files?.receptor_prepared : files?.ligand_prepared;
    const projectFile = isReceptor ? project.receptor.file : project.ligand.file;
    const displayFile = fileLine(preparedFile, projectFile);
    const fileName = displayFile.split(/[\\/]/).filter(Boolean).pop() || "尚未选择 PDBQT";
    const isReady = Boolean(projectFile && (preparedFile?.exists ?? true));

    return (
      <article className="preparation-target-row">
        <div className="preparation-target-identity">
          <span className="preparation-target-icon"><FileArrowUp aria-hidden="true" size={24} /></span>
          <div>
            <span>{label}</span>
            <strong>{fileName}</strong>
            <small>PDBQT</small>
          </div>
        </div>

        <Suspense fallback={<div className="structure-mini-preview structure-mini-preview-loading">正在加载 3D 预览…</div>}>
          <StructureMiniPreview
            fileKind={isReceptor ? "receptor_prepared" : "ligand_prepared"}
            label={label}
            projectDir={project.project_dir}
            refreshKey={previewRevision}
          />
        </Suspense>

        <div className="preparation-target-actions">
          <div className={`preparation-file-check ${isReady ? "is-ready" : "is-missing"}`}>
            {isReady ? <CheckCircle aria-hidden="true" size={18} weight="fill" /> : <Info aria-hidden="true" size={18} weight="fill" />}
            <div>
              <strong>{isReady ? "文件检查通过" : "等待 PDBQT"}</strong>
              <span>{preparedFile?.size ? `${preparedFile.size.toLocaleString()} B` : statusLabel(prep?.status)}</span>
            </div>
          </div>

          {mode === "existing" ? (
            <>
              <ActionButton variant="primary" disabled={isBusy} onClick={() => onOpenImportPdbqt(project)}>
                <FolderOpen aria-hidden="true" size={16} /> 选择文件
              </ActionButton>
              <ActionButton disabled={isBusy} onClick={() => void validateTarget(target)}>检查文件</ActionButton>
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
              <ActionButton variant="primary" disabled={isBusy} onClick={() => void prepareTarget(target)}>
                {isReceptor ? "准备受体输入" : "准备配体输入"}
              </ActionButton>
              <ActionButton disabled={isBusy} onClick={() => void validateTarget(target)}>检查条件</ActionButton>
            </>
          )}

          <AdvancedDetails summary="查看详情">
            <dl className="meta-list">
              <div><dt>原始输入</dt><dd><code>{fileLine(rawFile, isReceptor ? project.receptor.raw_file : project.ligand.raw_file)}</code></dd></div>
              <div><dt>准备方法</dt><dd>{prep?.method ?? "外部或手动导入"}</dd></div>
              <div><dt>日志</dt><dd><code>{prep?.log_file || "未生成"}</code></dd></div>
            </dl>
            <div className="button-row">
              <ActionButton variant="text" onClick={() => void loadLog(target)} disabled={isBusy}>读取日志</ActionButton>
              <ActionButton variant="text" onClick={() => void resetTarget(target)} disabled={isBusy}>重置状态</ActionButton>
            </div>
          </AdvancedDetails>
        </div>
      </article>
    );
  };

  return (
    <PageShell labelledBy="preparation-title" className="preparation-workspace-page">
      <PageHero
        eyebrow="结构准备 · STRUCTURE PREPARATION"
        title="结构准备"
        titleId="preparation-title"
        description="导入或准备受体与配体 PDBQT 文件，为 AutoDock Vina 对接做好准备。"
        actions={<ActionButton onClick={() => void reloadStatus()} disabled={isBusy}>刷新状态</ActionButton>}
      />

      <ModeTabs
        id="preparation-mode-tabs"
        label="结构准备方式"
        active={mode}
        onChange={setMode}
        options={[
          { id: "existing", label: "已有 PDBQT" },
          { id: "raw", label: "从原始结构准备" },
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
                <span>自动准备需要 Python、RDKit 与 Meeko；你也可以返回获取原始结构。</span>
              </div>
              <ActionButton onClick={onBack}>获取原始结构</ActionButton>
            </div>
          ) : (
            <button className="preparation-drop-zone" type="button" onClick={() => onOpenImportPdbqt(project)}>
              <FileArrowUp aria-hidden="true" size={20} />
              <span>选择或拖放 PDBQT 文件；导入后会保存到当前项目。</span>
            </button>
          )}

          <div className="preparation-feedback">
            <ScientificDisclaimer kind="preparation" />
            {message || rawError ? <CommandResultPanel title="准备状态" message={message} rawError={rawError} /> : null}
          </div>

          <footer className="preparation-action-bar">
            <p>自动准备结果仍需人工检查质子化、电荷、构象和缺失残基。</p>
            <div>
              <ActionButton onClick={() => void reloadStatus()} disabled={isBusy}>保存并检查</ActionButton>
              <ActionButton variant="primary" disabled={!readyForBox} onClick={() => onOpenBoxSetup(project)}>
                继续设置搜索范围
              </ActionButton>
            </div>
          </footer>
        </MainPanel>

        <RightRail className="preparation-context-rail">
          <RightRailSection title="当前输入">
            <dl className="mode-context-list">
              <div><dt>受体</dt><dd>{project.receptor.file || "未选择"}</dd></div>
              <div><dt>配体</dt><dd>{project.ligand.file || "未选择"}</dd></div>
            </dl>
          </RightRailSection>

          <RightRailSection title="文件检查">
            <div className="preparation-check-list">
              <span className={project.receptor.file ? "ready" : "missing"}><CheckCircle aria-hidden="true" size={16} weight="fill" /> 受体 PDBQT</span>
              <span className={project.ligand.file ? "ready" : "missing"}><CheckCircle aria-hidden="true" size={16} weight="fill" /> 配体 PDBQT</span>
            </div>
          </RightRailSection>

          <RightRailSection title="工具状态">
            <AdvancedDetails summary={mode === "existing" ? "当前模式无需转换" : "查看准备工具"}>
              <dl className="mode-context-list">
                <div><dt>Python</dt><dd>{statusLabel(tools?.python?.status)} · {toolVersion(tools?.python)}</dd></div>
                <div><dt>RDKit</dt><dd>{statusLabel(tools?.rdkit?.status)} · {capabilityLine(tools?.rdkit, "sdf_inline_read")}</dd></div>
                <div><dt>Meeko</dt><dd>{statusLabel(tools?.meeko?.status)}</dd></div>
              </dl>
            </AdvancedDetails>
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>{readyForBox ? "设置对接搜索范围，然后在同一工作台复核 Vina 参数。" : "先补全受体与配体 PDBQT。"}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
