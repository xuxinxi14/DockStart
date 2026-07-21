import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { CheckCircle, SpinnerGap } from "@phosphor-icons/react";

import type { DockStartProject } from "../types";
import { startFlexibleReceptorTask, waitForBackgroundTask } from "../utils/backgroundTasks";
import ActionButton from "./ActionButton";
import AdvancedDetails from "./AdvancedDetails";
import StatusBadge from "./StatusBadge";
import "../styles/flexible-receptor.css";

type FlexibleStatus = {
  ok: boolean;
  mode?: "rigid" | "flexible";
  effective_mode?: "rigid" | "flexible";
  flexible_ready?: boolean;
  flexible_receptor?: {
    preparation_id?: string;
    selected_residues?: Array<string | { selector?: string; residue_name?: string }>;
    rigid_file?: string;
    flex_file?: string;
  } | null;
  project?: DockStartProject | null;
  message?: string;
  error?: { code?: string; message?: string; raw_error?: string; suggestion?: string };
};

type Props = {
  project: DockStartProject;
  disabled?: boolean;
  onProjectChange: (project: DockStartProject) => void;
};

function parse(raw: string): FlexibleStatus {
  return JSON.parse(raw) as FlexibleStatus;
}

function residueLabel(value: string | { selector?: string; residue_name?: string }): string {
  if (typeof value === "string") return value;
  return [value.selector, value.residue_name].filter(Boolean).join(" ");
}

export default function FlexibleReceptorPanel({ project, disabled = false, onProjectChange }: Props) {
  const [status, setStatus] = useState<FlexibleStatus | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");

  const residues = useMemo(
    () => [...new Set(input.split(/[\s,;，；]+/).map((item) => item.trim()).filter(Boolean))],
    [input],
  );

  const refresh = useCallback(async (quiet = false) => {
    try {
      const next = parse(await invoke<string>("get_flexible_receptor_status", { projectDir: project.project_dir }));
      setStatus(next);
      if (!quiet) setMessage(next.message || "柔性受体状态已刷新。");
      if (next.error?.raw_error) setRawError(next.error.raw_error);
    } catch (error) {
      if (!quiet) setRawError(error instanceof Error ? error.message : String(error));
    }
  }, [project.project_dir]);

  useEffect(() => {
    setStatus(null);
    setInput("");
    setMessage("");
    setRawError("");
    void refresh(true);
  }, [refresh]);

  const validate = async () => {
    if (!residues.length) return;
    setBusy(true);
    setRawError("");
    try {
      const result = parse(await invoke<string>("validate_flexible_receptor", {
        projectDir: project.project_dir,
        residues,
        maxResidues: 8,
      }));
      if (!result.ok) throw new Error(result.error?.message || "柔性残基检查失败。");
      setMessage(`已确认 ${residues.length} 个残基可进入柔性准备。`);
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const prepare = async () => {
    if (!residues.length) return;
    setBusy(true);
    setRawError("");
    try {
      const task = await startFlexibleReceptorTask(project.project_dir, residues, 8);
      const completed = await waitForBackgroundTask(task.task_id, (next) => {
        setMessage(next.progress.message || next.message || "正在准备柔性受体…");
      });
      const result = completed.result_json ? parse(completed.result_json) : null;
      if (completed.status !== "finished" || !result?.ok) {
        throw new Error(result?.error?.message || completed.error || "柔性受体准备失败。");
      }
      if (result.project) onProjectChange(result.project);
      setMessage(result.message || "柔性受体已准备并激活。");
      await refresh(true);
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const setMode = async (mode: "rigid" | "flexible") => {
    setBusy(true);
    setRawError("");
    try {
      const result = parse(await invoke<string>("set_receptor_docking_mode", {
        projectDir: project.project_dir,
        mode,
      }));
      if (!result.ok) throw new Error(result.error?.message || "受体模式切换失败。");
      if (result.project) onProjectChange(result.project);
      setMessage(result.message || "受体模式已切换。");
      await refresh(true);
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const selected = status?.flexible_receptor?.selected_residues ?? [];
  return (
    <section className="run-cockpit-card flexible-receptor-panel">
      <div className="run-cockpit-section-heading">
        <div>
          <span className="run-cockpit-kicker">高级协议</span>
          <h2>有限柔性侧链</h2>
        </div>
        <StatusBadge tone={status?.effective_mode === "flexible" ? "ok" : "muted"}>
          {status?.effective_mode === "flexible" ? "柔性模式" : "刚性模式"}
        </StatusBadge>
      </div>

      <div className="flexible-receptor-body">
        <div>
          <p>仅开放少量口袋侧链，受体主链保持刚性。必须使用项目中的原始 PDB，不从 PDBQT 猜测残基。</p>
          <label>
            <span>柔性残基（最多 8 个）</span>
            <input
              value={input}
              disabled={disabled || busy}
              placeholder="例如 A:315, A:381 或 A:315:B"
              onChange={(event) => setInput(event.target.value)}
            />
          </label>
          <small>格式：链 ID:残基编号[:插入码]。当前版本先提供精确文本选择，三维点选后续加入。</small>
        </div>
        <div className="flexible-receptor-actions">
          <ActionButton variant="secondary" disabled={disabled || busy || !residues.length || residues.length > 8} onClick={() => void validate()}>
            检查选择
          </ActionButton>
          <ActionButton variant="primary" disabled={disabled || busy || !residues.length || residues.length > 8} onClick={() => void prepare()}>
            {busy ? <SpinnerGap className="run-monitor-spinner" size={16} /> : <CheckCircle size={16} />}
            准备并启用
          </ActionButton>
        </div>
      </div>

      {status?.flexible_ready ? (
        <div className="flexible-receptor-ready">
          <div>
            <strong>{status.flexible_receptor?.preparation_id || "已验证的柔性受体"}</strong>
            <span>{selected.map(residueLabel).join("、") || "残基记录可用"}</span>
          </div>
          <div className="flexible-receptor-actions">
            <ActionButton variant={status.effective_mode === "rigid" ? "primary" : "secondary"} disabled={disabled || busy} onClick={() => void setMode("rigid")}>使用刚性</ActionButton>
            <ActionButton variant={status.effective_mode === "flexible" ? "primary" : "secondary"} disabled={disabled || busy} onClick={() => void setMode("flexible")}>使用柔性</ActionButton>
          </div>
        </div>
      ) : null}

      {message ? <p className="flexible-receptor-message" role="status">{message}</p> : null}
      {rawError ? <AdvancedDetails summary="柔性受体诊断"><pre>{rawError}</pre></AdvancedDetails> : null}
    </section>
  );
}
