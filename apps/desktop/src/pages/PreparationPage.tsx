import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
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

type PreparationPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenImportPdbqt: (project: DockStartProject) => void;
  onOpenViewer: (project: DockStartProject) => void;
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
  onOpenViewer,
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

  useEffect(() => {
    setProject(initialProject);
  }, [initialProject]);

  const applyResponse = useCallback(
    (next: PreparationStatusResponse, fallbackMessage: string) => {
      setResponse(next);
      if (next.project) {
        setProject(next.project);
        onProjectChange(next.project);
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

  const renderPrepCard = (target: PreparationTarget, prep: PreparationResult | undefined) => {
    const isReceptor = target === "receptor";
    const rawFile = isReceptor ? files?.receptor_raw : files?.ligand_raw;
    const preparedFile = isReceptor ? files?.receptor_prepared : files?.ligand_prepared;
    return (
      <article className="task-card">
        <div className="section-card-header">
          <h2>{isReceptor ? "受体准备" : "配体准备"}</h2>
          <StatusBadge tone={statusTone(prep?.status)}>{statusLabel(prep?.status)}</StatusBadge>
        </div>
        <dl className="meta-list">
          <div>
            <dt>输入文件</dt>
            <dd><code>{fileLine(rawFile, isReceptor ? project.receptor.raw_file : project.ligand.raw_file)}</code></dd>
          </div>
          <div>
            <dt>输出文件</dt>
            <dd><code>{fileLine(preparedFile, isReceptor ? project.receptor.file : project.ligand.file)}</code></dd>
          </div>
        </dl>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={isReceptor ? overwriteReceptor : overwriteLigand}
            onChange={(event) => (isReceptor ? setOverwriteReceptor(event.target.checked) : setOverwriteLigand(event.target.checked))}
          />
          覆盖已有 PDBQT
        </label>
        <div className="button-row">
          <ActionButton variant="primary" disabled={isBusy} onClick={() => void prepareTarget(target)}>
            {isReceptor ? "准备受体输入" : "准备配体输入"}
          </ActionButton>
          <ActionButton onClick={() => void validateTarget(target)} disabled={isBusy}>检查条件</ActionButton>
          <ActionButton variant="text" onClick={() => void loadLog(target)} disabled={isBusy}>查看日志</ActionButton>
        </div>
        <AdvancedDetails>
          <dl className="meta-list">
            <div>
              <dt>方法</dt>
              <dd>{prep?.method ?? "未记录"}</dd>
            </div>
            <div>
              <dt>stdout</dt>
              <dd><code>{prep?.stdout_file || "未生成"}</code></dd>
            </div>
            <div>
              <dt>stderr</dt>
              <dd><code>{prep?.stderr_file || "未生成"}</code></dd>
            </div>
            <div>
              <dt>log</dt>
              <dd><code>{prep?.log_file || "未生成"}</code></dd>
            </div>
          </dl>
          <ActionButton variant="text" onClick={() => void resetTarget(target)} disabled={isBusy}>重置状态</ActionButton>
        </AdvancedDetails>
      </article>
    );
  };

  return (
    <section className="workbench-page" aria-labelledby="preparation-title">
      <header className="page-hero">
        <div className="page-hero-main">
          <p className="eyebrow">工作流 2</p>
          <h1 id="preparation-title">准备 Vina 输入</h1>
          <p>把 raw 结构准备为 PDBQT，或确认已经导入的 Vina 输入文件。</p>
        </div>
        <div className="page-hero-actions">
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
          <ActionButton onClick={() => void reloadStatus()} disabled={isBusy}>刷新状态</ActionButton>
        </div>
      </header>

      <SectionCard title="工具链状态">
        <div className="status-strip">
          <article className="metric-card">
            <span>Python</span>
            <strong>{statusLabel(tools?.python?.status)} · {toolVersion(tools?.python)}</strong>
          </article>
          <article className="metric-card">
            <span>RDKit</span>
            <strong>{statusLabel(tools?.rdkit?.status)} · {toolVersion(tools?.rdkit)}</strong>
          </article>
          <article className="metric-card">
            <span>Meeko</span>
            <strong>{statusLabel(tools?.meeko?.status)} · {toolVersion(tools?.meeko)}</strong>
          </article>
        </div>
        <AdvancedDetails>
          <dl className="meta-list">
            <div>
              <dt>RDKit SDF 读取</dt>
              <dd>{capabilityLine(tools?.rdkit, "sdf_inline_read")}</dd>
            </div>
            <div>
              <dt>Meeko 配体准备</dt>
              <dd>{capabilityLine(tools?.meeko, "ligand_preparation")}</dd>
            </div>
            <div>
              <dt>Meeko 受体准备</dt>
              <dd>{capabilityLine(tools?.meeko, "receptor_preparation")}</dd>
            </div>
          </dl>
        </AdvancedDetails>
      </SectionCard>

      <div className="two-column-grid">
        {renderPrepCard("receptor", receptorPrep)}
        {renderPrepCard("ligand", ligandPrep)}
      </div>

      <div className="next-step-strip">
        <div>
          <strong>{readyForBox ? "下一步：设置搜索范围" : "先补全受体和配体 PDBQT"}</strong>
          <p>自动准备结果仍需人工检查；也可以手动导入 PDBQT。</p>
        </div>
        <div className="button-row end">
          <ActionButton onClick={() => onOpenImportPdbqt(project)}>导入 PDBQT</ActionButton>
          <ActionButton onClick={() => onOpenViewer(project)}>打开 3D 查看</ActionButton>
          <ActionButton variant="primary" disabled={!readyForBox} onClick={() => onOpenBoxSetup(project)}>
            进入设置搜索范围
          </ActionButton>
        </div>
      </div>

      <ScientificDisclaimer kind="preparation" />
      <CommandResultPanel title="准备结果" message={message} rawError={rawError} />
    </section>
  );
}
