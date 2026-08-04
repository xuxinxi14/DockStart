import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { CheckCircle, Crosshair, SpinnerGap } from "@phosphor-icons/react";

import type { DockStartProject, ViewerStructureResult } from "../types";
import { startFlexibleReceptorTask, waitForBackgroundTask } from "../utils/backgroundTasks";
import ActionButton from "./ActionButton";
import AdvancedDetails from "./AdvancedDetails";
import StatusBadge from "./StatusBadge";

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
    scientific_review?: {
      allow_bad_res?: boolean;
      detected_bad_residues?: string[];
    };
  } | null;
  review?: {
    allow_bad_res?: boolean;
    bad_residues?: string[];
    acknowledged_bad_residues?: string[];
    selector?: string;
    altlocs?: string[];
  };
  identity_context?: FlexibleReceptorIdentityContext;
  project?: DockStartProject | null;
  message?: string;
  error?: { code?: string; message?: string; raw_error?: string; suggestion?: string };
};

type IdentityResidue = {
  selector: string;
  meeko_id?: string;
  author?: {
    chain_id?: string;
    sequence_id?: number;
    insertion_code?: string;
    component_id?: string;
  };
  label?: {
    chain_id?: string;
    sequence_id?: string;
    component_id?: string;
    entity_id?: string;
  };
  bridge?: {
    chain_id?: string;
    residue_number?: number;
    insertion_code?: string;
    residue_name?: string;
  };
  alternate_locations?: {
    ids?: string[];
    requires_explicit_choice?: boolean;
    occupancy?: Record<string, {
      atom_count?: number;
      minimum?: number;
      maximum?: number;
      sum?: number;
    }>;
  };
};

export type FlexibleReceptorIdentityContext = {
  ok: boolean;
  source_format: "pdb" | "mmcif";
  source_raw_file: string;
  source_sha256: string;
  selection_context_sha256: string;
  identity_contract_sha256?: string;
  model?: {
    id?: string;
    count?: number;
    selection_policy?: string;
  };
  residues?: IdentityResidue[];
  viewer?: ViewerStructureResult;
  message?: string;
  error?: { code?: string; message?: string; raw_error?: string; suggestion?: string };
};

type Props = {
  project: DockStartProject;
  disabled?: boolean;
  onProjectChange: (project: DockStartProject) => void;
  pickedResidue?: string;
  pickedResidueToken?: number;
  pickedResidueContextSha256?: string;
  selectionActive?: boolean;
  onSelectionActiveChange?: (active: boolean) => void;
  onResiduesChange?: (residues: string[]) => void;
  onIdentityContextChange?: (context: FlexibleReceptorIdentityContext | null) => void;
};

function parse(raw: string): FlexibleStatus {
  return JSON.parse(raw) as FlexibleStatus;
}

function parseIdentity(raw: string): FlexibleReceptorIdentityContext {
  return JSON.parse(raw) as FlexibleReceptorIdentityContext;
}

function residueLabel(value: string | { selector?: string; residue_name?: string }): string {
  if (typeof value === "string") return value;
  return [value.selector, value.residue_name].filter(Boolean).join(" ");
}

export default function FlexibleReceptorPanel({
  project,
  disabled = false,
  onProjectChange,
  pickedResidue = "",
  pickedResidueToken = 0,
  pickedResidueContextSha256 = "",
  selectionActive = false,
  onSelectionActiveChange,
  onResiduesChange,
  onIdentityContextChange,
}: Props) {
  const [status, setStatus] = useState<FlexibleStatus | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [badResidues, setBadResidues] = useState<string[]>([]);
  const [badResiduesConfirmed, setBadResiduesConfirmed] = useState(false);
  const [identityContext, setIdentityContext] = useState<FlexibleReceptorIdentityContext | null>(null);
  const [resolvedAltlocs, setResolvedAltlocs] = useState<Record<string, string>>({});
  const [pointSelectionContextSha256, setPointSelectionContextSha256] = useState("");
  const [viewMode, setViewMode] = useState<"rigid" | "flexible">(
    project.docking_protocol?.mode === "flexible" ? "flexible" : "rigid",
  );

  const residues = useMemo(
    () => [...new Set(input.split(/[\s,;，；]+/).map((item) => item.trim()).filter(Boolean))],
    [input],
  );

  useEffect(() => {
    onResiduesChange?.(residues);
  }, [onResiduesChange, residues]);

  const residueSignature = residues.join("|");
  useEffect(() => {
    setBadResidues([]);
    setBadResiduesConfirmed(false);
    setResolvedAltlocs((current) => Object.fromEntries(
      Object.entries(current).filter(([selector]) => residues.includes(selector)),
    ));
  }, [project.project_dir, residueSignature]);

  useEffect(() => {
    if (!pickedResidue) return;
    if (
      !pickedResidueContextSha256
      || pickedResidueContextSha256 !== identityContext?.selection_context_sha256
    ) {
      setRawError("3D 点选结果没有绑定当前受体身份，已拒绝加入。请重新进入 3D 点选。");
      return;
    }
    if (
      pointSelectionContextSha256
      && pointSelectionContextSha256 !== pickedResidueContextSha256
    ) {
      setRawError("本次点选过程中受体身份已变化，请清空并重新选择柔性残基。");
      return;
    }
    setPointSelectionContextSha256(pickedResidueContextSha256);
    setInput((current) => {
      const next = [...new Set([...current.split(/[\s,;，；]+/).filter(Boolean), pickedResidue])];
      return next.slice(0, 8).join(", ");
    });
    setViewMode("flexible");
  }, [
    identityContext?.selection_context_sha256,
    pickedResidue,
    pickedResidueContextSha256,
    pickedResidueToken,
    pointSelectionContextSha256,
  ]);

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

  const loadIdentityContext = useCallback(async () => {
    const next = parseIdentity(await invoke<string>(
      "get_flexible_receptor_identity_context",
      { projectDir: project.project_dir },
    ));
    if (!next.ok || !next.selection_context_sha256 || !next.viewer?.ok) {
      throw new Error(
        [
          next.error?.message || "无法建立柔性残基身份审查上下文。",
          next.error?.suggestion,
          next.error?.raw_error,
        ].filter(Boolean).join("\n"),
      );
    }
    setIdentityContext(next);
    onIdentityContextChange?.(next);
    return next;
  }, [onIdentityContextChange, project.project_dir]);

  useEffect(() => {
    setStatus(null);
    setInput("");
    setMessage("");
    setRawError("");
    setBadResidues([]);
    setBadResiduesConfirmed(false);
    setIdentityContext(null);
    setResolvedAltlocs({});
    setPointSelectionContextSha256("");
    onIdentityContextChange?.(null);
    void refresh(true);
  }, [onIdentityContextChange, refresh]);

  const identityResidues = useMemo(
    () => new Map((identityContext?.residues ?? []).map((residue) => [residue.selector, residue])),
    [identityContext?.residues],
  );
  const altlocRequirements = useMemo(
    () => residues
      .map((selector) => identityResidues.get(selector))
      .filter((residue): residue is IdentityResidue => Boolean(
        residue?.alternate_locations?.ids?.length,
      )),
    [identityResidues, residues],
  );

  const unresolvedAltlocs = altlocRequirements.filter(
    (residue) => !resolvedAltlocs[residue.selector],
  );

  const ensureCurrentIdentityContext = async () => {
    const next = await loadIdentityContext();
    if (
      pointSelectionContextSha256
      && pointSelectionContextSha256 !== next.selection_context_sha256
    ) {
      throw new Error("受体在 3D 点选后发生变化。请重新进入 3D 点选并确认残基。");
    }
    return next;
  };

  const startPointSelection = async () => {
    setBusy(true);
    setRawError("");
    try {
      await loadIdentityContext();
      onSelectionActiveChange?.(true);
      setMessage("已进入身份绑定的原始受体视图；点选残基后点击“选择完成”。");
    } catch (error) {
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const validate = async () => {
    if (!residues.length) return;
    setBusy(true);
    setRawError("");
    try {
      const context = await ensureCurrentIdentityContext();
      const currentRequirements = residues
        .map((selector) => (context.residues ?? []).find((item) => item.selector === selector))
        .filter((residue): residue is IdentityResidue => Boolean(
          residue?.alternate_locations?.ids?.length,
        ));
      const missingChoices = currentRequirements.filter(
        (residue) => !resolvedAltlocs[residue.selector],
      );
      if (missingChoices.length) {
        setMessage(`请先为 ${missingChoices.map((item) => item.selector).join("、")} 选择 altloc。`);
        return;
      }
      const result = parse(await invoke<string>("validate_flexible_receptor", {
        projectDir: project.project_dir,
        residues,
        maxResidues: 8,
        resolvedAltlocs,
        selectionContextSha256: pointSelectionContextSha256 || context.selection_context_sha256,
      }));
      if (!result.ok) {
        if (result.identity_context?.ok) {
          setIdentityContext(result.identity_context);
          onIdentityContextChange?.(result.identity_context);
        }
        setRawError(
          [
            result.error?.message || "柔性残基检查失败。",
            result.error?.suggestion,
            result.error?.raw_error,
          ].filter(Boolean).join("\n"),
        );
        return;
      }
      setMessage(`已确认 ${residues.length} 个残基选择有效；受体模板完整性将在严格准备时检查。`);
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
      const context = await ensureCurrentIdentityContext();
      const currentRequirements = residues
        .map((selector) => (context.residues ?? []).find((item) => item.selector === selector))
        .filter((residue): residue is IdentityResidue => Boolean(
          residue?.alternate_locations?.ids?.length,
        ));
      const missingChoices = currentRequirements.filter(
        (residue) => !resolvedAltlocs[residue.selector],
      );
      if (missingChoices.length) {
        setMessage(`请先为 ${missingChoices.map((item) => item.selector).join("、")} 选择 altloc。`);
        return;
      }
      const allowBadRes = badResidues.length > 0 && badResiduesConfirmed;
      const task = await startFlexibleReceptorTask(
        project.project_dir,
        residues,
        8,
        allowBadRes,
        allowBadRes ? badResidues : [],
        resolvedAltlocs,
        pointSelectionContextSha256 || context.selection_context_sha256,
      );
      const completed = await waitForBackgroundTask(task.task_id, (next) => {
        setMessage(next.progress.message || next.message || "正在准备柔性受体…");
      });
      const result = completed.result_json ? parse(completed.result_json) : null;
      if (completed.status !== "finished" || !result?.ok) {
        const reviewResidues = result?.review?.bad_residues ?? [];
        if (reviewResidues.length) {
          setBadResidues(reviewResidues);
          setBadResiduesConfirmed(false);
          setMessage(`严格模式检测到 ${reviewResidues.length} 个坏残基；项目仍保持刚性，请审阅后决定是否允许 Meeko 忽略。`);
          setRawError("");
          return;
        }
        setRawError(
          [
            result?.error?.message || completed.error || "柔性受体准备失败。",
            result?.error?.suggestion,
            result?.error?.raw_error,
          ].filter(Boolean).join("\n"),
        );
        return;
      }
      if (result.project) onProjectChange(result.project);
      setMessage(result.message || "柔性受体已准备并激活。");
      setBadResidues([]);
      setBadResiduesConfirmed(false);
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
          <span className="run-cockpit-kicker">受体协议</span>
          <h2>受体柔性设置</h2>
        </div>
        <StatusBadge tone={status?.effective_mode === "flexible" ? "ok" : "muted"}>
          {status?.effective_mode === "flexible" ? "柔性模式" : "刚性模式"}
        </StatusBadge>
      </div>

      <div className="flexible-receptor-content">
        <nav className="flexible-mode-switch" aria-label="受体柔性模式">
          <button type="button" className={viewMode === "rigid" ? "active" : ""} onClick={() => setViewMode("rigid")}>刚性受体</button>
          <button type="button" className={viewMode === "flexible" ? "active" : ""} onClick={() => setViewMode("flexible")}>有限柔性</button>
        </nav>

        {viewMode === "flexible" ? <div className="flexible-receptor-body">
          <div className="flexible-receptor-editor">
            <label>
              <span>柔性残基（最多 8 个）</span>
              <input
                value={input}
                disabled={disabled || busy}
                placeholder="例如 A:315, A:381 或 A:315:B"
                onChange={(event) => {
                  setInput(event.target.value);
                  setPointSelectionContextSha256("");
                }}
              />
            </label>
            <small>格式：A:315；带插入码时使用 A:315:B。</small>
            {altlocRequirements.length ? (
              <div className="flexible-altloc-review">
                <strong>替代构象选择</strong>
                {altlocRequirements.map((residue) => {
                  const ids = residue.alternate_locations?.ids ?? [];
                  const occupancy = residue.alternate_locations?.occupancy ?? {};
                  return (
                    <label key={residue.selector}>
                      <span>{residue.selector} {residue.author?.component_id || ""}</span>
                      <select
                        value={resolvedAltlocs[residue.selector] || ""}
                        disabled={disabled || busy}
                        onChange={(event) => setResolvedAltlocs((current) => ({
                          ...current,
                          [residue.selector]: event.target.value,
                        }))}
                      >
                        <option value="">请选择 altloc</option>
                        {ids.map((id) => {
                          const facts = occupancy[id];
                          const range = facts
                            ? `${facts.minimum ?? "?"}–${facts.maximum ?? "?"}`
                            : "未记录";
                          return <option key={id} value={id}>{id}（occupancy {range}）</option>;
                        })}
                      </select>
                    </label>
                  );
                })}
                {unresolvedAltlocs.length ? <small>DockStart 不会按 occupancy 自动选择。</small> : null}
              </div>
            ) : null}
            <div className="flexible-receptor-actions">
              <ActionButton
                variant={selectionActive ? "primary" : "secondary"}
                disabled={disabled || busy || selectionActive}
                onClick={() => void startPointSelection()}
              >
                <Crosshair size={16} />{selectionActive ? "正在点选" : "3D 点选"}
              </ActionButton>
              <ActionButton variant="secondary" disabled={disabled || busy || !residues.length || residues.length > 8} onClick={() => void validate()}>
                检查选择
              </ActionButton>
              <ActionButton
                variant="primary"
                disabled={
                  disabled
                  || busy
                  || !residues.length
                  || residues.length > 8
                  || (badResidues.length > 0 && !badResiduesConfirmed)
                }
                onClick={() => void prepare()}
              >
                {busy ? <SpinnerGap className="run-monitor-spinner" size={16} /> : <CheckCircle size={16} />}
                准备并启用
              </ActionButton>
            </div>
            {badResidues.length ? (
              <div className="flexible-bad-residue-review" role="alert">
                <strong>Meeko 将删除 {badResidues.length} 个无法匹配模板的残基</strong>
                <p>严格准备已停止，项目仍使用刚性受体。请核对完整清单；确认后再次点击“准备并启用”。</p>
                <AdvancedDetails summary="查看将被忽略的完整残基列表">
                  <pre>{badResidues.join(", ")}</pre>
                </AdvancedDetails>
                <label className="flexible-bad-residue-confirm">
                  <input
                    type="checkbox"
                    checked={badResiduesConfirmed}
                    disabled={disabled || busy}
                    onChange={(event) => setBadResiduesConfirmed(event.target.checked)}
                  />
                  <span>我已核对完整列表，同意本次忽略这些残基。</span>
                </label>
              </div>
            ) : null}
          </div>
        </div> : (
          <div className="flexible-receptor-rigid-summary">
            <strong>刚性受体</strong>
            {status?.effective_mode === "flexible" ? <ActionButton disabled={disabled || busy} onClick={() => void setMode("rigid")}>切回刚性受体</ActionButton> : <StatusBadge tone="ok">当前使用</StatusBadge>}
          </div>
        )}

        {viewMode === "flexible" && status?.flexible_ready ? (
          <div className="flexible-receptor-ready">
            <div className="flexible-receptor-ready-summary">
              <span className="flexible-receptor-ready-label">已验证柔性受体</span>
              <strong>{status.flexible_receptor?.preparation_id || "准备记录可用"}</strong>
              <dl>
                <div><dt>柔性残基</dt><dd>{selected.map(residueLabel).join("、") || "残基记录可用"}</dd></div>
                {status.flexible_receptor?.scientific_review?.allow_bad_res ? (
                  <div><dt>已确认忽略</dt><dd>{status.flexible_receptor.scientific_review.detected_bad_residues?.length ?? 0} 个残基</dd></div>
                ) : null}
              </dl>
            </div>
            <div className="flexible-receptor-actions">
              <ActionButton variant={status.effective_mode === "rigid" ? "primary" : "secondary"} disabled={disabled || busy} onClick={() => void setMode("rigid")}>使用刚性</ActionButton>
              <ActionButton variant={status.effective_mode === "flexible" ? "primary" : "secondary"} disabled={disabled || busy} onClick={() => void setMode("flexible")}>使用柔性</ActionButton>
            </div>
          </div>
        ) : null}

        {message && !(viewMode === "flexible" && status?.flexible_ready) ? <p className="flexible-receptor-message" role="status">{message}</p> : null}
        {rawError ? <AdvancedDetails summary="柔性受体诊断"><pre>{rawError}</pre></AdvancedDetails> : null}
      </div>
    </section>
  );
}
