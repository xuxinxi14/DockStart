import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { CheckCircle, Database, WarningCircle } from "@phosphor-icons/react";
import type {
  AutoGridMapsDefaults,
  AutoGridMapsDefaultsResponse,
  AutoGridMapsStatusResponse,
  DockStartProject,
  ProjectResponse,
} from "../types";
import ActionButton from "./ActionButton";
import AdvancedDetails from "./AdvancedDetails";
import OperationLoadingDialog from "./OperationLoadingDialog";
import PathInput from "./PathInput";
import StatusBadge from "./StatusBadge";

type AutoGridMapsPanelProps = {
  project: DockStartProject;
  disabled?: boolean;
  disabledReason?: string;
  onProjectChange: (project: DockStartProject) => void;
  onStatusChange?: () => void;
};

type GridForm = {
  spacing: string;
  x: string;
  y: string;
  z: string;
  receptorTypes: string;
  ligandTypes: string;
  parameterFile: string;
};

function formFromDefaults(defaults: AutoGridMapsDefaults): GridForm {
  return {
    spacing: String(defaults.spacing),
    x: String(defaults.grid_points.x),
    y: String(defaults.grid_points.y),
    z: String(defaults.grid_points.z),
    receptorTypes: defaults.receptor_atom_types.join(" "),
    ligandTypes: defaults.ligand_atom_types.join(" "),
    parameterFile: defaults.parameter_file || "",
  };
}

function errorText(payload: {
  error?: { message?: string; raw_error?: string; suggestion?: string } | null;
  message?: string;
}): { message: string; detail: string } {
  return {
    message: payload.error?.message || payload.message || "操作失败。",
    detail: [payload.error?.raw_error, payload.error?.suggestion].filter(Boolean).join("\n"),
  };
}

export default function AutoGridMapsPanel({
  project,
  disabled = false,
  disabledReason = "",
  onProjectChange,
  onStatusChange,
}: AutoGridMapsPanelProps) {
  const isAd4 = project.docking_protocol?.engine === "ad4_maps";
  const [defaults, setDefaults] = useState<AutoGridMapsDefaults | null>(null);
  const [status, setStatus] = useState<AutoGridMapsStatusResponse | null>(null);
  const [form, setForm] = useState<GridForm | null>(null);
  const [importFile, setImportFile] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [busyAction, setBusyAction] = useState<"" | "load" | "switch" | "generate" | "import">("");

  const load = useCallback(async () => {
    setBusyAction((current) => current || "load");
    try {
      const [defaultsRaw, statusRaw] = await Promise.all([
        invoke<string>("get_autogrid_maps_defaults", { projectDir: project.project_dir }),
        invoke<string>("get_autogrid_maps_status", { projectDir: project.project_dir }),
      ]);
      const defaultsResponse = JSON.parse(defaultsRaw) as AutoGridMapsDefaultsResponse;
      const statusResponse = JSON.parse(statusRaw) as AutoGridMapsStatusResponse;
      setStatus(statusResponse);
      if (defaultsResponse.ok && defaultsResponse.defaults) {
        setDefaults(defaultsResponse.defaults);
        setForm((current) => current ?? formFromDefaults(defaultsResponse.defaults!));
      } else {
        const error = errorText(defaultsResponse);
        setMessage(error.message);
        setRawError(error.detail);
      }
    } catch (error) {
      setMessage("无法读取 AutoDock4 maps 状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction((current) => (current === "load" ? "" : current));
    }
  }, [project.project_dir]);

  useEffect(() => {
    setDefaults(null);
    setForm(null);
    setStatus(null);
    setImportFile("");
    setMessage("");
    setRawError("");
    void load();
  }, [load]);

  const parsedGrid = useMemo(() => {
    if (!form) return null;
    const spacing = Number(form.spacing);
    const points = [Number(form.x), Number(form.y), Number(form.z)];
    if (!Number.isFinite(spacing) || spacing < 0.1 || spacing > 1) return null;
    if (!points.every((value) => Number.isInteger(value) && value >= 2 && value <= 126 && value % 2 === 0)) return null;
    if (!form.receptorTypes.trim() || !form.ligandTypes.trim()) return null;
    return { spacing, points };
  }, [form]);

  const switchProtocol = async (protocol: "vina" | "ad4_maps") => {
    if (disabled || busyAction) return;
    setBusyAction("switch");
    setMessage("");
    setRawError("");
    try {
      const raw = await invoke<string>("set_scoring_protocol", {
        projectDir: project.project_dir,
        protocol,
      });
      const response = JSON.parse(raw) as ProjectResponse;
      if (!response.ok || !response.project) {
        const error = errorText(response);
        setMessage(error.message);
        setRawError(error.detail);
        return;
      }
      onProjectChange(response.project);
      setMessage(response.message || "评分协议已切换。");
      await load();
      onStatusChange?.();
    } catch (error) {
      setMessage("无法切换评分协议。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction("");
    }
  };

  const generate = async () => {
    if (!form || !parsedGrid || disabled || busyAction) return;
    setBusyAction("generate");
    setMessage("");
    setRawError("");
    try {
      const raw = await invoke<string>("generate_autogrid_maps", {
        projectDir: project.project_dir,
        optionsJson: JSON.stringify({
          spacing: parsedGrid.spacing,
          grid_points: { x: parsedGrid.points[0], y: parsedGrid.points[1], z: parsedGrid.points[2] },
          receptor_atom_types: form.receptorTypes,
          ligand_atom_types: form.ligandTypes,
          parameter_file: form.parameterFile,
        }),
      });
      const response = JSON.parse(raw) as AutoGridMapsStatusResponse;
      if (!response.ok || !response.project) {
        const error = errorText(response);
        setMessage(error.message);
        setRawError(error.detail);
        return;
      }
      onProjectChange(response.project);
      setMessage(response.message || "AutoDock4 maps 已生成。");
      await load();
      onStatusChange?.();
    } catch (error) {
      setMessage("AutoGrid4 maps 生成失败。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction("");
    }
  };

  const importMaps = async () => {
    if (!importFile.trim() || disabled || busyAction) return;
    setBusyAction("import");
    setMessage("");
    setRawError("");
    try {
      const raw = await invoke<string>("import_autogrid_maps", {
        projectDir: project.project_dir,
        fldFile: importFile.trim(),
      });
      const response = JSON.parse(raw) as AutoGridMapsStatusResponse;
      if (!response.ok || !response.project) {
        const error = errorText(response);
        setMessage(error.message);
        setRawError(error.detail);
        return;
      }
      onProjectChange(response.project);
      setMessage(response.message || "AutoDock4 maps 已导入。");
      await load();
      onStatusChange?.();
    } catch (error) {
      setMessage("AutoDock4 maps 导入失败。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction("");
    }
  };

  const mapGrid = status?.manifest?.grid;
  const gridPoints = mapGrid?.grid_points;
  const mapFiles = status?.manifest?.maps?.files ?? [];

  return (
    <section className="run-cockpit-card ad4-maps-card" aria-labelledby="ad4-maps-title">
      <div className="run-cockpit-section-heading">
        <div>
          <span className="run-cockpit-kicker">评分协议</span>
          <h2 id="ad4-maps-title">Vina / AutoDock4 Maps</h2>
        </div>
        <StatusBadge tone={isAd4 ? (status?.ready ? "ok" : "warning") : "info"}>
          {isAd4 ? (status?.ready ? "AD4 maps 已就绪" : "AD4 maps 未就绪") : "Vina / Vinardo"}
        </StatusBadge>
      </div>

      <div className="ad4-protocol-tabs" role="group" aria-label="选择评分协议">
        <button
          type="button"
          className={!isAd4 ? "active" : ""}
          aria-pressed={!isAd4}
          disabled={disabled || Boolean(busyAction)}
          onClick={() => void switchProtocol("vina")}
        >
          Vina / Vinardo
        </button>
        <button
          type="button"
          className={isAd4 ? "active" : ""}
          aria-pressed={isAd4}
          disabled={disabled || Boolean(busyAction)}
          onClick={() => void switchProtocol("ad4_maps")}
        >
          AutoDock4（maps）
        </button>
      </div>

      {!isAd4 ? (
        <div className="ad4-protocol-summary">
          <Database aria-hidden="true" size={21} />
          <div>
            <strong>当前使用标准 Vina 工作流</strong>
            <p>Vina 与 Vinardo 可在下方参数区选择。AutoDock4 使用独立 maps、结果文件与报告。</p>
          </div>
        </div>
      ) : (
        <div className="ad4-maps-workspace">
          <div className={`ad4-maps-status ${status?.ready ? "ready" : "blocked"}`}>
            {status?.ready ? <CheckCircle aria-hidden="true" size={22} weight="fill" /> : <WarningCircle aria-hidden="true" size={22} weight="fill" />}
            <div>
              <strong>{status?.ready ? `${status.map_set_id} 可用于运行` : "需要生成或导入完整 maps"}</strong>
              <p>
                {status?.ready && gridPoints
                  ? `${gridPoints.x} × ${gridPoints.y} × ${gridPoints.z} 点 · ${mapGrid?.spacing ?? "—"} Å · ${mapFiles.length} 个文件`
                  : status?.issues?.[0] || status?.message || "正在读取 maps 状态。"}
              </p>
            </div>
            <StatusBadge tone={status?.tool?.status === "ok" ? "ok" : "warning"}>
              {`AutoGrid4 ${status?.tool?.status === "ok" ? status.tool.version || "可用" : "未配置"}`}
            </StatusBadge>
          </div>

          {form ? (
            <>
              <div className="ad4-grid-fields">
                <label>
                  <span>网格间距（Å）</span>
                  <input value={form.spacing} disabled={disabled || Boolean(busyAction)} onChange={(event) => setForm({ ...form, spacing: event.target.value })} />
                  <small>标准值 0.375</small>
                </label>
                {(["x", "y", "z"] as const).map((axis) => (
                  <label key={axis}>
                    <span>{axis.toUpperCase()} 轴点数</span>
                    <input value={form[axis]} disabled={disabled || Boolean(busyAction)} onChange={(event) => setForm({ ...form, [axis]: event.target.value })} />
                    <small>2–126 的偶数</small>
                  </label>
                ))}
              </div>
              <div className="ad4-type-fields">
                <label>
                  <span>受体原子类型</span>
                  <input value={form.receptorTypes} disabled={disabled || Boolean(busyAction)} onChange={(event) => setForm({ ...form, receptorTypes: event.target.value })} />
                </label>
                <label>
                  <span>配体原子类型</span>
                  <input value={form.ligandTypes} disabled={disabled || Boolean(busyAction)} onChange={(event) => setForm({ ...form, ligandTypes: event.target.value })} />
                </label>
              </div>
              <label className="ad4-path-field">
                <span>自定义参数文件（可选）</span>
                <PathInput
                  value={form.parameterFile}
                  onChange={(value) => setForm({ ...form, parameterFile: value })}
                  disabled={disabled || Boolean(busyAction)}
                  mode="file"
                  title="选择 AutoGrid 参数文件"
                  placeholder="留空使用 AutoGrid4 默认参数库"
                />
              </label>
              <div className="ad4-map-actions">
                <ActionButton
                  variant="primary"
                  disabled={disabled || Boolean(busyAction) || !parsedGrid || status?.tool?.status !== "ok"}
                  onClick={() => void generate()}
                >
                  生成并校验 maps
                </ActionButton>
                <span>或</span>
                <PathInput
                  value={importFile}
                  onChange={setImportFile}
                  disabled={disabled || Boolean(busyAction)}
                  mode="file"
                  filters={[{ name: "AutoDock4 field", extensions: ["fld"] }]}
                  title="选择 .maps.fld"
                  placeholder="导入已有 .maps.fld（同目录需有 GPF 与全部 map 文件）"
                  ariaLabel="待导入的 AutoDock4 maps field 文件"
                />
                <ActionButton disabled={disabled || Boolean(busyAction) || !importFile.trim()} onClick={() => void importMaps()}>
                  导入并校验
                </ActionButton>
              </div>
              {!parsedGrid ? <p className="ad4-inline-error">间距须为 0.1–1.0 Å；每轴点数须为 2–126 的偶数。</p> : null}
            </>
          ) : null}

          <p className="ad4-license-note">
            AutoGrid4 是外部 GPL 工具，不随 DockStart 安装包分发。标准 maps 协议仅开放非金属刚性受体。
          </p>
          {status?.manifest_file ? (
            <AdvancedDetails summary="maps 记录">
              <dl className="ad4-manifest-details">
                <div><dt>Manifest</dt><dd>{status.manifest_file}</dd></div>
                <div><dt>Prefix</dt><dd>{status.maps_prefix}</dd></div>
                <div><dt>来源</dt><dd>{status.manifest?.source || "未记录"}</dd></div>
                <div><dt>配体类型</dt><dd>{status.manifest?.maps?.ligand_atom_types?.join(", ") || "未记录"}</dd></div>
              </dl>
            </AdvancedDetails>
          ) : null}
        </div>
      )}

      {disabled && disabledReason ? <p className="ad4-disabled-reason">{disabledReason}</p> : null}
      {message ? <p className="run-inline-message" role={rawError ? "alert" : "status"}>{message}</p> : null}
      {rawError ? <AdvancedDetails summary="查看诊断"><pre>{rawError}</pre></AdvancedDetails> : null}

      <OperationLoadingDialog
        open={busyAction === "generate" || busyAction === "import"}
        title={busyAction === "generate" ? "正在生成 AutoDock4 maps" : "正在导入并校验 maps"}
        message={busyAction === "generate" ? "AutoGrid4 正在计算网格文件，请等待完成。" : "正在复制文件并核对受体、网格与 SHA256。"}
        detail="窗口会在操作完成后自动关闭。"
      />
    </section>
  );
}
