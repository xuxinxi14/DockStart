import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { CheckCircle, WarningCircle } from "@phosphor-icons/react";
import type {
  Ad4ZnStatusResponse,
  AutoGridMapsDefaults,
  AutoGridMapsDefaultsResponse,
  AutoGridMapsStatusResponse,
  DockStartProject,
  ProjectResponse,
} from "../types";
import ActionButton from "./ActionButton";
import Ad4ZnProtocolPanel, { getAd4ZnWorkflowReadiness } from "./Ad4ZnProtocolPanel";
import AdvancedDetails from "./AdvancedDetails";
import OperationLoadingDialog from "./OperationLoadingDialog";
import PathInput from "./PathInput";
import StatusBadge from "./StatusBadge";
import VinaMapsPanel from "./VinaMapsPanel";

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

function parseGridForm(form: GridForm | null): { spacing: number; points: number[] } | null {
  if (!form) return null;
  const spacing = Number(form.spacing);
  const points = [Number(form.x), Number(form.y), Number(form.z)];
  if (!Number.isFinite(spacing) || spacing < 0.1 || spacing > 1) return null;
  if (!points.every((value) => Number.isInteger(value) && value >= 2 && value <= 126 && value % 2 === 0)) return null;
  if (!form.receptorTypes.trim() || !form.ligandTypes.trim()) return null;
  return { spacing, points };
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
  const isAd4Zn = isAd4 && project.docking_protocol?.protocol_id === "ad4zn_beta";
  const [defaults, setDefaults] = useState<AutoGridMapsDefaults | null>(null);
  const [status, setStatus] = useState<AutoGridMapsStatusResponse | null>(null);
  const [ad4ZnStatus, setAd4ZnStatus] = useState<Ad4ZnStatusResponse | null>(null);
  const [ad4ZnMapsStatus, setAd4ZnMapsStatus] = useState<AutoGridMapsStatusResponse | null>(null);
  const [ad4ZnRefreshToken, setAd4ZnRefreshToken] = useState(0);
  const [ad4ZnPanelBusy, setAd4ZnPanelBusy] = useState(false);
  const [form, setForm] = useState<GridForm | null>(null);
  const [importFile, setImportFile] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [busyAction, setBusyAction] = useState<"" | "load" | "switch" | "generate" | "import">("");

  const load = useCallback(async () => {
    if (!isAd4 || isAd4Zn) return;
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
  }, [isAd4, isAd4Zn, project.project_dir]);

  useEffect(() => {
    setDefaults(null);
    setForm(null);
    setStatus(null);
    setAd4ZnStatus(null);
    setAd4ZnMapsStatus(null);
    setAd4ZnPanelBusy(false);
    setImportFile("");
    setMessage("");
    setRawError("");
    if (isAd4 && !isAd4Zn) {
      void load();
    } else {
      setBusyAction((current) => (current === "load" ? "" : current));
    }
  }, [isAd4, isAd4Zn, load]);

  const parsedGrid = useMemo(() => parseGridForm(form), [form]);
  const protocolInteractionBusy = Boolean(busyAction) || ad4ZnPanelBusy;

  const switchProtocol = async (protocol: "vina" | "ad4_maps" | "ad4zn_beta") => {
    if (disabled || protocolInteractionBusy) return;
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
      onStatusChange?.();
    } catch (error) {
      setMessage("无法切换评分协议。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction("");
    }
  };

  const generate = async () => {
    if ((!isAd4Zn && (!form || !parsedGrid)) || disabled || busyAction) return;
    setBusyAction("generate");
    setMessage("");
    setRawError("");
    try {
      let generationForm = form;
      let generationGrid = parsedGrid;
      if (isAd4Zn) {
        const defaultsRaw = await invoke<string>("get_autogrid_maps_defaults", {
          projectDir: project.project_dir,
        });
        const defaultsResponse = JSON.parse(defaultsRaw) as AutoGridMapsDefaultsResponse;
        if (!defaultsResponse.ok || !defaultsResponse.defaults) {
          const error = errorText(defaultsResponse);
          setMessage(error.message);
          setRawError(error.detail);
          return;
        }
        generationForm = formFromDefaults(defaultsResponse.defaults);
        generationGrid = parseGridForm(generationForm);
      }
      if (!generationForm || !generationGrid) {
        setMessage("当前 AutoGrid 参数不完整。");
        setRawError("请检查网格间距、点数和原子类型。");
        return;
      }
      const raw = await invoke<string>("generate_autogrid_maps", {
        projectDir: project.project_dir,
        optionsJson: JSON.stringify({
          spacing: generationGrid.spacing,
          grid_points: { x: generationGrid.points[0], y: generationGrid.points[1], z: generationGrid.points[2] },
          receptor_atom_types: generationForm.receptorTypes,
          ligand_atom_types: generationForm.ligandTypes,
          parameter_file: generationForm.parameterFile,
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
      setMessage(response.message || (isAd4Zn ? "AutoDock4Zn maps 已生成。" : "AutoDock4 maps 已生成。"));
      if (isAd4Zn) {
        setAd4ZnRefreshToken((current) => current + 1);
      } else {
        await load();
      }
      onStatusChange?.();
    } catch (error) {
      setMessage("AutoGrid4 maps 生成失败。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction("");
    }
  };

  const handleAd4ZnStatus = useCallback((
    protocolStatus: Ad4ZnStatusResponse | null,
    mapsStatus: AutoGridMapsStatusResponse | null,
  ) => {
    setAd4ZnStatus(protocolStatus);
    setAd4ZnMapsStatus(mapsStatus);
  }, []);

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
  const { workflowReady: ad4ZnReady } = getAd4ZnWorkflowReadiness(
    ad4ZnStatus,
    ad4ZnMapsStatus,
  );

  return (
    <section className="run-cockpit-card ad4-maps-card" aria-labelledby="ad4-maps-title">
      <div className="run-cockpit-section-heading">
        <div>
          <span className="run-cockpit-kicker">评分协议</span>
          <h2 id="ad4-maps-title">Vina / AutoDock4 Maps</h2>
        </div>
        <StatusBadge tone={isAd4 ? ((isAd4Zn ? ad4ZnReady : status?.ready) ? "ok" : "warning") : "info"}>
          {isAd4
            ? isAd4Zn
              ? ad4ZnReady ? "AutoDock4Zn beta 已就绪" : "AutoDock4Zn beta 未就绪"
              : status?.ready ? "AD4 maps 已就绪" : "AD4 maps 未就绪"
            : "Vina / Vinardo"}
        </StatusBadge>
      </div>

      <div className="ad4-protocol-tabs" role="group" aria-label="选择评分协议">
        <button
          type="button"
          className={!isAd4 ? "active" : ""}
          aria-pressed={!isAd4}
          disabled={disabled || protocolInteractionBusy}
          onClick={() => void switchProtocol("vina")}
        >
          Vina / Vinardo
        </button>
        <button
          type="button"
          className={isAd4 ? "active" : ""}
          aria-pressed={isAd4}
          disabled={disabled || protocolInteractionBusy}
          onClick={() => {
            if (!isAd4) void switchProtocol("ad4_maps");
          }}
        >
          AutoDock4（maps）
        </button>
      </div>

      {!isAd4 ? (
        <VinaMapsPanel
          project={project}
          disabled={disabled || Boolean(busyAction)}
          disabledReason={disabledReason}
          onProjectChange={onProjectChange}
          onStatusChange={onStatusChange}
        />
      ) : (
        <>
          <div className="ad4-subprotocol-switch">
            <div>
              <strong>AutoDock4 maps 协议</strong>
              <small>标准 AD4 用于非金属体系；AD4Zn beta 只用于 Zn。</small>
            </div>
            <div role="group" aria-label="选择 AutoDock4 maps 子协议">
              <button
                type="button"
                className={!isAd4Zn ? "active" : ""}
                aria-pressed={!isAd4Zn}
                disabled={disabled || protocolInteractionBusy}
                onClick={() => {
                  if (isAd4Zn) void switchProtocol("ad4_maps");
                }}
              >
                标准 AD4
              </button>
              <button
                type="button"
                className={isAd4Zn ? "active" : ""}
                aria-pressed={isAd4Zn}
                disabled={disabled || protocolInteractionBusy}
                onClick={() => {
                  if (!isAd4Zn) void switchProtocol("ad4zn_beta");
                }}
              >
                AD4Zn beta
              </button>
            </div>
          </div>

          {isAd4Zn ? (
            <Ad4ZnProtocolPanel
              project={project}
              disabled={disabled || Boolean(busyAction)}
              disabledReason={disabledReason}
              mapsBusy={busyAction === "generate"}
              refreshToken={ad4ZnRefreshToken}
              onBusyChange={setAd4ZnPanelBusy}
              onProjectChange={onProjectChange}
              onGenerateMaps={generate}
              onProtocolStatus={handleAd4ZnStatus}
              onStatusChange={onStatusChange}
            />
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

          {status && status.tool?.status !== "ok" ? (
            <p className="ad4-inline-error" role="alert">
              尚未配置可用的 AutoGrid4，当前不能生成 maps。请打开右上角“工具链”，配置 AutoGrid4 路径并重新检测；已有完整 maps 仍可从下方导入。
            </p>
          ) : null}

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
        </>
      )}

      {isAd4 && !isAd4Zn && disabled && disabledReason ? <p className="ad4-disabled-reason">{disabledReason}</p> : null}
      {message ? <p className="run-inline-message" role={rawError ? "alert" : "status"}>{message}</p> : null}
      {rawError ? <AdvancedDetails summary="查看诊断"><pre>{rawError}</pre></AdvancedDetails> : null}

      <OperationLoadingDialog
        open={busyAction === "generate" || busyAction === "import"}
        title={busyAction === "generate"
          ? isAd4Zn ? "正在生成 AutoDock4Zn maps" : "正在生成 AutoDock4 maps"
          : "正在导入并校验 maps"}
        message={busyAction === "generate"
          ? isAd4Zn
            ? "AutoGrid4 正在使用 TZ 受体与 AD4Zn 参数计算网格文件。"
            : "AutoGrid4 正在计算网格文件，请等待完成。"
          : "正在复制文件并核对受体、网格与 SHA256。"}
        detail="窗口会在操作完成后自动关闭。"
      />
    </section>
  );
}
