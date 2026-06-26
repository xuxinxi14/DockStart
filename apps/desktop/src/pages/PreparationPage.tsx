import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type {
  DockStartProject,
  PreparationToolCapabilityResult,
  PreparationToolStatusResponse,
  PreparationResult,
  PreparationStatusResponse,
  PreparationTarget,
  RunFileStatus,
  ToolCheckResult,
} from "../types";

type PreparationPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenImportPdbqt: (project: DockStartProject) => void;
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

function statusText(status: string | undefined): string {
  const labels: Record<string, string> = {
    ok: "可用",
    missing: "缺失",
    error: "错误",
    unknown: "未知",
    not_started: "未开始",
    checking: "检查中",
    ready: "可准备",
    running: "准备中",
    finished: "已完成",
    failed: "失败",
    empty: "空文件",
  };
  return labels[status ?? ""] ?? "未知";
}

function statusClass(status: string | undefined): string {
  if (status === "ok" || status === "ready" || status === "finished") {
    return "status-ok";
  }
  if (status === "missing" || status === "not_started" || status === "unknown") {
    return "status-missing";
  }
  return "status-error";
}

function fileLine(file: RunFileStatus | undefined, fallback: string): string {
  if (!file) {
    return fallback || "未记录";
  }
  return `${file.path || fallback || "未记录"} · ${statusText(file.status)}`;
}

function toolLine(tool: ToolCheckResult | PreparationToolCapabilityResult | undefined): string {
  if (!tool) {
    return "未检测";
  }
  return `${statusText(tool.status)} · ${tool.version || "无版本"} · ${tool.source || "unknown"}`;
}

function capabilityLine(
  tool: PreparationToolCapabilityResult | undefined,
  capabilityKey: string,
): string {
  const capability = tool?.capabilities?.[capabilityKey];
  if (!capability) {
    return "未检测";
  }
  return `${statusText(capability.status)} · ${capability.message}`;
}

export default function PreparationPage({
  project: initialProject,
  onBack,
  onOpenImportPdbqt,
  onProjectChange,
}: PreparationPageProps) {
  const [project, setProject] = useState(initialProject);
  const [response, setResponse] = useState<PreparationStatusResponse | null>(null);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [nextAction, setNextAction] = useState("");
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
        // Preparation status already contains best-effort tool results; keep the page usable.
      }
      try {
        const rawWorkflowPayload = await invoke<string>("get_project_workflow_status", {
          projectDir: project.project_dir,
        });
        const workflowStatus = JSON.parse(rawWorkflowPayload) as { next_recommended_action?: string };
        setNextAction(workflowStatus.next_recommended_action ?? "");
      } catch {
        setNextAction("");
      }
      applyResponse(parsed, "准备状态已刷新。");
    } catch (error) {
      setMessage("前端未能调用准备状态命令。");
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
      applyResponse(parsePreparationResponse(rawPayload), `${target} 准备前置检查完成。`);
    } catch (error) {
      setMessage("前端未能调用准备前置检查命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const resetTarget = async (target: PreparationTarget) => {
    const label = target === "receptor" ? "受体" : "配体";
    if (!window.confirm(`确定重置${label}准备状态吗？prepared PDBQT 文件不会被删除。`)) {
      return;
    }
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("reset_preparation_status", {
        projectDir: project.project_dir,
        target,
      });
      applyResponse(parsePreparationResponse(rawPayload), `${label}准备状态已重置。`);
    } catch (error) {
      setMessage("前端未能调用准备状态重置命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const prepareLigand = async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("prepare_ligand_pdbqt", {
        projectDir: project.project_dir,
        overwrite: overwriteLigand,
      });
      applyResponse(parsePreparationResponse(rawPayload), "ligand PDBQT 自动准备已完成。");
    } catch (error) {
      setMessage("前端未能调用 ligand PDBQT 自动准备命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const prepareReceptor = async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("prepare_receptor_pdbqt", {
        projectDir: project.project_dir,
        overwrite: overwriteReceptor,
      });
      applyResponse(parsePreparationResponse(rawPayload), "receptor PDBQT 自动准备已完成。");
    } catch (error) {
      setMessage("前端未能调用 receptor PDBQT 自动准备命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const loadReceptorLog = async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("load_receptor_preparation_log", {
        projectDir: project.project_dir,
      });
      const parsed = JSON.parse(rawPayload) as { ok: boolean; message?: string; stderr?: string; stdout?: string; log?: string; error?: { message: string; raw_error: string } };
      setMessage(parsed.message ?? parsed.error?.message ?? "receptor preparation 日志已读取。");
      setRawError([parsed.stderr, parsed.stdout, parsed.log, parsed.error?.raw_error].filter(Boolean).join("\n\n"));
    } catch (error) {
      setMessage("前端未能读取 receptor preparation 日志。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const loadLigandLog = async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("load_ligand_preparation_log", {
        projectDir: project.project_dir,
      });
      const parsed = JSON.parse(rawPayload) as { ok: boolean; message?: string; stderr?: string; stdout?: string; log?: string; error?: { message: string; raw_error: string } };
      setMessage(parsed.message ?? parsed.error?.message ?? "ligand preparation 日志已读取。");
      setRawError([parsed.stderr, parsed.stdout, parsed.log, parsed.error?.raw_error].filter(Boolean).join("\n\n"));
    } catch (error) {
      setMessage("前端未能读取 ligand preparation 日志。");
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

  return (
    <section className="project-page" aria-labelledby="preparation-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回原始结构页
      </button>

      <div className="page-heading">
        <p className="eyebrow">PreparationPage</p>
        <h1 id="preparation-title">PDBQT 自动准备</h1>
        <p>
          V0.3 支持在工具链可用时，从 raw 文件尝试准备 receptor / ligand PDBQT。当前页面只提供最小入口和状态提示。
        </p>
      </div>

      <div className="project-summary">
        <span>当前项目</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <div className="warning-note">
        自动准备结果仍需用户判断，不代表质子化、电荷、构象、链选择或受体结构处理一定科学正确，也不代表药效判断。
      </div>

      {nextAction ? (
        <div className="settings-message">
          下一步建议：{nextAction}
        </div>
      ) : null}

      <div className="import-grid">
        <article className="import-card">
          <h2>工具链检测</h2>
          <dl className="tool-meta">
            <div>
              <dt>Python</dt>
              <dd>{toolLine(tools?.python)}</dd>
            </div>
            <div>
              <dt>RDKit</dt>
              <dd>{toolLine(tools?.rdkit)}</dd>
            </div>
            <div>
              <dt>RDKit SDF 读取</dt>
              <dd>{capabilityLine(tools?.rdkit, "sdf_inline_read")}</dd>
            </div>
            <div>
              <dt>Meeko</dt>
              <dd>{toolLine(tools?.meeko)}</dd>
            </div>
            <div>
              <dt>Meeko 配体准备能力</dt>
              <dd>{capabilityLine(tools?.meeko, "ligand_preparation")}</dd>
            </div>
            <div>
              <dt>Meeko 受体准备能力</dt>
              <dd>{capabilityLine(tools?.meeko, "receptor_preparation")}</dd>
            </div>
          </dl>
        </article>

        <article className="import-card">
          <div className="tool-card-header">
            <h2>受体准备状态</h2>
            <span className={`status-badge ${statusClass(receptorPrep?.status)}`}>{statusText(receptorPrep?.status)}</span>
          </div>
          <dl className="tool-meta">
            <div>
              <dt>receptor raw file</dt>
              <dd><code>{fileLine(files?.receptor_raw, project.receptor.raw_file)}</code></dd>
            </div>
            <div>
              <dt>receptor prepared file</dt>
              <dd><code>{fileLine(files?.receptor_prepared, project.receptor.file)}</code></dd>
            </div>
            <div>
              <dt>方法</dt>
              <dd>{receptorPrep?.method ?? "未记录"}</dd>
            </div>
            <div>
              <dt>stdout</dt>
              <dd><code>{receptorPrep?.stdout_file || "未生成"}</code></dd>
            </div>
            <div>
              <dt>stderr</dt>
              <dd><code>{receptorPrep?.stderr_file || "未生成"}</code></dd>
            </div>
            <div>
              <dt>log</dt>
              <dd><code>{receptorPrep?.log_file || "未生成"}</code></dd>
            </div>
          </dl>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={overwriteReceptor}
              onChange={(event) => setOverwriteReceptor(event.target.checked)}
            />
            覆盖已有 prepared/receptor.pdbqt
          </label>
          <div className="toolbar project-toolbar">
            <button className="primary-button" type="button" disabled={isBusy} onClick={() => void prepareReceptor()}>
              准备 receptor PDBQT
            </button>
            <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void validateTarget("receptor")}>
              检查受体准备条件
            </button>
            <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void loadReceptorLog()}>
              读取受体准备日志
            </button>
            <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void resetTarget("receptor")}>
              重置受体准备状态
            </button>
          </div>
        </article>

        <article className="import-card">
          <div className="tool-card-header">
            <h2>配体准备状态</h2>
            <span className={`status-badge ${statusClass(ligandPrep?.status)}`}>{statusText(ligandPrep?.status)}</span>
          </div>
          <dl className="tool-meta">
            <div>
              <dt>ligand raw file</dt>
              <dd><code>{fileLine(files?.ligand_raw, project.ligand.raw_file)}</code></dd>
            </div>
            <div>
              <dt>ligand prepared file</dt>
              <dd><code>{fileLine(files?.ligand_prepared, project.ligand.file)}</code></dd>
            </div>
            <div>
              <dt>方法</dt>
              <dd>{ligandPrep?.method ?? "未记录"}</dd>
            </div>
            <div>
              <dt>stdout</dt>
              <dd><code>{ligandPrep?.stdout_file || "未生成"}</code></dd>
            </div>
            <div>
              <dt>stderr</dt>
              <dd><code>{ligandPrep?.stderr_file || "未生成"}</code></dd>
            </div>
            <div>
              <dt>log</dt>
              <dd><code>{ligandPrep?.log_file || "未生成"}</code></dd>
            </div>
          </dl>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={overwriteLigand}
              onChange={(event) => setOverwriteLigand(event.target.checked)}
            />
            覆盖已有 prepared/ligand.pdbqt
          </label>
          <div className="toolbar project-toolbar">
            <button className="primary-button" type="button" disabled={isBusy} onClick={() => void prepareLigand()}>
              准备 ligand PDBQT
            </button>
            <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void validateTarget("ligand")}>
              检查配体准备条件
            </button>
            <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void loadLigandLog()}>
              读取配体准备日志
            </button>
            <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void resetTarget("ligand")}>
              重置配体准备状态
            </button>
          </div>
        </article>
      </div>

      <div className="toolbar project-toolbar">
        <button className="text-button inline" type="button" disabled={isBusy} onClick={() => void reloadStatus()}>
          重新读取准备状态
        </button>
        <button className="secondary-button" type="button" onClick={() => onOpenImportPdbqt(project)}>
          去导入 prepared PDBQT
        </button>
      </div>

      {message ? <p className="settings-message">{message}</p> : null}
      {rawError ? (
        <details className="raw-error">
          <summary>查看 raw_error</summary>
          <pre>{rawError}</pre>
        </details>
      ) : null}
    </section>
  );
}
