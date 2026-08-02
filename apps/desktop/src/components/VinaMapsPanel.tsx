import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  CheckCircle,
  Database,
  FileArrowDown,
  GridFour,
  WarningCircle,
} from "@phosphor-icons/react";
import type {
  DockStartProject,
  VinaMapsGridSource,
  VinaMapsStatusResponse,
} from "../types";
import {
  buildRawVinaMapsImportOptions,
  vinaMapsCompatibilityIssues,
  vinaMapsGridSummary,
  type RawMapsConfirmations,
} from "../utils/vinaMaps";
import ActionButton from "./ActionButton";
import AdvancedDetails from "./AdvancedDetails";
import OperationLoadingDialog from "./OperationLoadingDialog";
import PathInput from "./PathInput";
import StatusBadge from "./StatusBadge";

type VinaMapsPanelProps = {
  project: DockStartProject;
  disabled?: boolean;
  disabledReason?: string;
  onProjectChange: (project: DockStartProject) => void;
  onStatusChange?: () => void;
};

type BusyAction = "" | "load" | "mode" | "generate" | "import";
type ImportKind = "dockstart_manifest" | "raw_maps";

const emptyConfirmations: RawMapsConfirmations = {
  receptor: false,
  box: false,
  scoring: false,
};

function responseError(response: VinaMapsStatusResponse): {
  message: string;
  detail: string;
} {
  return {
    message: response.error?.message || response.message || "Vina maps 操作失败。",
    detail: [
      response.error?.raw_error,
      response.error?.suggestion,
    ].filter(Boolean).join("\n"),
  };
}

function numberText(value: unknown, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";
}

function hashText(value: string | undefined): string {
  if (!value) return "未记录";
  return value.length > 20 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

export default function VinaMapsPanel({
  project,
  disabled = false,
  disabledReason = "",
  onProjectChange,
  onStatusChange,
}: VinaMapsPanelProps) {
  const [status, setStatus] = useState<VinaMapsStatusResponse | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction>("");
  const [importKind, setImportKind] = useState<ImportKind>("dockstart_manifest");
  const [sourcePath, setSourcePath] = useState("");
  const [confirmations, setConfirmations] =
    useState<RawMapsConfirmations>(emptyConfirmations);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");

  const load = useCallback(async () => {
    setBusyAction((current) => current || "load");
    try {
      const raw = await invoke<string>("get_vina_maps_status", {
        projectDir: project.project_dir,
      });
      const response = JSON.parse(raw) as VinaMapsStatusResponse;
      setStatus(response);
      if (!response.ok) {
        const error = responseError(response);
        setMessage(error.message);
        setRawError(error.detail);
      }
    } catch (error) {
      setMessage("无法读取 Vina maps 状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction((current) => (current === "load" ? "" : current));
    }
  }, [project.project_dir]);

  useEffect(() => {
    setStatus(null);
    setSourcePath("");
    setImportKind("dockstart_manifest");
    setConfirmations(emptyConfirmations);
    setMessage("");
    setRawError("");
    void load();
  }, [load]);

  const gridSource: VinaMapsGridSource =
    status?.grid_source
    ?? project.docking_protocol?.grid_source
    ?? "receptor";
  const mapsActive = gridSource === "precomputed_maps";
  const compatibilityIssues = useMemo(
    () => vinaMapsCompatibilityIssues(project),
    [project],
  );
  const mapsModeAllowed = compatibilityIssues.length === 0;
  const manifest = status?.manifest ?? null;
  const gridSummary = vinaMapsGridSummary(manifest);
  const mapCount = manifest?.maps?.map_count ?? manifest?.maps?.files?.length ?? 0;
  const context = status?.current_context;
  const rawImportOptions = useMemo(
    () => buildRawVinaMapsImportOptions(status, confirmations),
    [confirmations, status],
  );
  const contextBox = context?.grid?.requested_box;
  const currentScoring =
    context?.scoring_function
    ?? manifest?.scoring_function
    ?? project.vina.scoring;
  const canActivateSaved =
    mapsModeAllowed
    && Boolean(status?.ready && status.map_set_id && manifest);
  const isBusy = Boolean(busyAction);

  const applyResponse = async (
    response: VinaMapsStatusResponse,
    fallbackMessage: string,
  ) => {
    setStatus(response);
    if (!response.ok) {
      const error = responseError(response);
      setMessage(error.message);
      setRawError(error.detail);
      return false;
    }
    if (response.project) {
      onProjectChange(response.project);
    }
    setMessage(response.message || fallbackMessage);
    setRawError("");
    await load();
    onStatusChange?.();
    return true;
  };

  const setMode = async (mode: VinaMapsGridSource) => {
    if (
      disabled
      || isBusy
      || mode === gridSource
      || (mode === "precomputed_maps" && !canActivateSaved)
    ) {
      return;
    }
    setBusyAction("mode");
    setMessage("");
    setRawError("");
    try {
      const raw = await invoke<string>("set_vina_maps_mode", {
        projectDir: project.project_dir,
        mode,
      });
      await applyResponse(
        JSON.parse(raw) as VinaMapsStatusResponse,
        mode === "receptor" ? "已改为运行时计算网格。" : "已启用保存的 maps。",
      );
    } catch (error) {
      setMessage("无法切换 Vina 网格来源。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction("");
    }
  };

  const generate = async () => {
    if (disabled || isBusy || !mapsModeAllowed) return;
    setBusyAction("generate");
    setMessage("");
    setRawError("");
    try {
      const raw = await invoke<string>("generate_vina_maps", {
        projectDir: project.project_dir,
      });
      await applyResponse(
        JSON.parse(raw) as VinaMapsStatusResponse,
        "当前受体与 Box 的 Vina maps 已生成并启用。",
      );
    } catch (error) {
      setMessage("Vina maps 生成失败。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction("");
    }
  };

  const importMaps = async () => {
    if (
      disabled
      || isBusy
      || !mapsModeAllowed
      || !sourcePath.trim()
      || (importKind === "raw_maps" && !rawImportOptions)
    ) {
      return;
    }

    const options =
      importKind === "raw_maps"
        ? rawImportOptions
        : { activate: true };
    setBusyAction("import");
    setMessage("");
    setRawError("");
    try {
      const raw = await invoke<string>("import_vina_maps", {
        projectDir: project.project_dir,
        sourcePath: sourcePath.trim(),
        optionsJson: JSON.stringify(options),
      });
      const accepted = await applyResponse(
        JSON.parse(raw) as VinaMapsStatusResponse,
        "Vina maps 已导入、校验并启用。",
      );
      if (accepted) {
        setSourcePath("");
        setConfirmations(emptyConfirmations);
      }
    } catch (error) {
      setMessage("Vina maps 导入失败。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyAction("");
    }
  };

  return (
    <section className="vina-maps-panel" aria-labelledby="vina-maps-title">
      <header className="vina-maps-heading">
        <div>
          <span className="run-cockpit-kicker">Vina / Vinardo 网格</span>
          <h3 id="vina-maps-title">网格来源</h3>
        </div>
        <StatusBadge
          tone={mapsActive ? (status?.ready ? "ok" : "warning") : "info"}
        >
          {mapsActive
            ? status?.ready
              ? "已保存 maps"
              : "maps 不可用"
            : "实时计算"}
        </StatusBadge>
      </header>

      <div className="vina-maps-source-switch" role="group" aria-label="Vina 网格来源">
        <button
          type="button"
          className={!mapsActive ? "active" : ""}
          aria-pressed={!mapsActive}
          disabled={disabled || isBusy}
          onClick={() => void setMode("receptor")}
        >
          <Database aria-hidden="true" size={17} />
          <span><strong>实时计算</strong><small>由受体与当前 Box 建立</small></span>
        </button>
        <button
          type="button"
          className={mapsActive ? "active" : ""}
          aria-pressed={mapsActive}
          disabled={disabled || isBusy || !canActivateSaved}
          onClick={() => void setMode("precomputed_maps")}
          title={!canActivateSaved ? "需先生成或导入一组可用 maps" : undefined}
        >
          <GridFour aria-hidden="true" size={17} />
          <span><strong>已保存 maps</strong><small>读取已校验的网格文件</small></span>
        </button>
      </div>

      {mapsActive ? (
        <div className={`vina-maps-status ${status?.ready ? "ready" : "blocked"}`}>
          {status?.ready
            ? <CheckCircle aria-hidden="true" size={21} weight="fill" />
            : <WarningCircle aria-hidden="true" size={21} weight="fill" />}
          <div>
            <strong>
              {status?.ready
                ? `${status.map_set_id || "当前 maps"} 可用于运行`
                : "当前 maps 未通过运行检查"}
            </strong>
            <p>
              {status?.ready
                ? `${currentScoring === "vinardo" ? "Vinardo" : "Vina"} · ${mapCount} 个 map 文件 · ${gridSummary.spacing}`
                : status?.issues?.[0] || status?.message || "请重新生成或导入 maps。"}
            </p>
          </div>
        </div>
      ) : (
        <div className="vina-maps-runtime-summary">
          <Database aria-hidden="true" size={19} />
          <div>
            <strong>运行时计算网格</strong>
            <p>Vina 将使用当前受体、Box 与 {currentScoring === "vinardo" ? "Vinardo" : "Vina"} 评分函数。</p>
          </div>
        </div>
      )}

      {compatibilityIssues.length ? (
        <div className="vina-maps-compatibility" role="status">
          <WarningCircle aria-hidden="true" size={18} weight="fill" />
          <p>预计算 maps 首版仅用于刚性受体与全局对接：{compatibilityIssues.join("；")}。</p>
        </div>
      ) : null}

      <div className="vina-maps-actions-grid">
        <section className="vina-maps-action-card vina-maps-save-card">
          <div className="vina-maps-semantics" aria-label="预计算 maps 运行限制">
            <div>
              <span>grid-only</span>
              <span>no-refine 等效</span>
              <span>刚性受体</span>
              <span>全局对接</span>
            </div>
            <p>已保存 maps 模式不实时读取受体计算网格，也不执行最终受体原子精修。</p>
          </div>
          <div className="vina-maps-save-action">
            <header>
              <GridFour aria-hidden="true" size={18} />
              <div><strong>保存当前网格</strong><small>当前受体 · Box · {currentScoring === "vinardo" ? "Vinardo" : "Vina"}</small></div>
            </header>
            <ActionButton
              variant="primary"
              disabled={disabled || isBusy || !mapsModeAllowed}
              onClick={() => void generate()}
            >
              生成并启用
            </ActionButton>
          </div>
        </section>

        <section
          className={`vina-maps-action-card vina-maps-import-card ${importKind === "raw_maps" ? "is-raw-import" : ""}`.trim()}
        >
          <header>
            <FileArrowDown aria-hidden="true" size={18} />
            <div><strong>导入 maps</strong><small>DockStart manifest 或外部 raw maps</small></div>
          </header>
          <div className="vina-maps-import-kind" role="group" aria-label="maps 导入类型">
            <button
              type="button"
              className={importKind === "dockstart_manifest" ? "active" : ""}
              aria-pressed={importKind === "dockstart_manifest"}
              disabled={disabled || isBusy}
              onClick={() => {
                setImportKind("dockstart_manifest");
                setSourcePath("");
                setConfirmations(emptyConfirmations);
              }}
            >
              DockStart manifest
            </button>
            <button
              type="button"
              className={importKind === "raw_maps" ? "active" : ""}
              aria-pressed={importKind === "raw_maps"}
              disabled={disabled || isBusy}
              onClick={() => {
                setImportKind("raw_maps");
                setSourcePath("");
                setConfirmations(emptyConfirmations);
              }}
            >
              外部 raw maps
            </button>
          </div>
          <PathInput
            value={sourcePath}
            onChange={setSourcePath}
            disabled={disabled || isBusy}
            mode={importKind === "raw_maps" ? "directory" : "file"}
            filters={
              importKind === "dockstart_manifest"
                ? [{ name: "DockStart maps manifest", extensions: ["json"] }]
                : undefined
            }
            title={
              importKind === "raw_maps"
                ? "选择 raw maps 文件夹"
                : "选择 DockStart maps manifest"
            }
            placeholder={
              importKind === "raw_maps"
                ? "选择包含 *.map 的文件夹"
                : "选择 vina_maps_manifest.json"
            }
            ariaLabel="待导入的 Vina maps 来源"
          />

          {importKind === "raw_maps" ? (
            <div className="vina-maps-attestation">
              <div className="vina-maps-attestation-context">
                <div><span>受体</span><strong title={context?.receptor?.source_sha256}>{hashText(context?.receptor?.source_sha256)}</strong></div>
                <div>
                  <span>Box 中心</span>
                  <strong>
                    {numberText(contextBox?.center?.x)} / {numberText(contextBox?.center?.y)} / {numberText(contextBox?.center?.z)}
                  </strong>
                </div>
                <div>
                  <span>Box 尺寸</span>
                  <strong>
                    {numberText(contextBox?.size?.x)} / {numberText(contextBox?.size?.y)} / {numberText(contextBox?.size?.z)}
                  </strong>
                </div>
                <div><span>评分来源</span><strong>{currentScoring === "vinardo" ? "Vinardo" : "Vina"} · {context?.vina?.version || "版本未知"}</strong></div>
              </div>
              <div className="vina-maps-attestation-checks">
                <label>
                  <input
                    type="checkbox"
                    checked={confirmations.receptor}
                    disabled={disabled || isBusy}
                    onChange={(event) => setConfirmations({ ...confirmations, receptor: event.target.checked })}
                  />
                  raw maps 属于上方受体
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={confirmations.box}
                    disabled={disabled || isBusy}
                    onChange={(event) => setConfirmations({ ...confirmations, box: event.target.checked })}
                  />
                  网格范围与上方 Box 一致
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={confirmations.scoring}
                    disabled={disabled || isBusy}
                    onChange={(event) => setConfirmations({ ...confirmations, scoring: event.target.checked })}
                  />
                  评分函数与 Vina 来源一致
                </label>
              </div>
              {!rawImportOptions ? (
                <p className="vina-maps-attestation-note">
                  三项确认及当前受体、Vina 哈希齐全后才能导入。
                </p>
              ) : null}
            </div>
          ) : null}

          <ActionButton
            disabled={
              disabled
              || isBusy
              || !mapsModeAllowed
              || !sourcePath.trim()
              || (importKind === "raw_maps" && !rawImportOptions)
            }
            onClick={() => void importMaps()}
          >
            导入、校验并启用
          </ActionButton>
        </section>
      </div>

      {manifest && status?.manifest_file ? (
        <AdvancedDetails summary="maps manifest 详情" className="vina-maps-manifest">
          <dl>
            <div><dt>Manifest</dt><dd>{status.manifest_file}</dd></div>
            <div><dt>Map set</dt><dd>{status.map_set_id || manifest.map_set_id || "未记录"}</dd></div>
            <div><dt>来源</dt><dd>{manifest.source || "未记录"}</dd></div>
            <div><dt>评分函数</dt><dd>{manifest.scoring_function || "未记录"}</dd></div>
            <div><dt>中心</dt><dd>{gridSummary.center}</dd></div>
            <div><dt>尺寸</dt><dd>{gridSummary.size}</dd></div>
            <div><dt>网格点</dt><dd>{gridSummary.elements}</dd></div>
            <div><dt>Maps</dt><dd>{mapCount} 个 · {manifest.maps?.total_size_bytes ?? "—"} bytes</dd></div>
            <div><dt>受体 SHA256</dt><dd title={manifest.receptor?.source_sha256}>{hashText(manifest.receptor?.source_sha256)}</dd></div>
            <div><dt>Vina SHA256</dt><dd title={manifest.vina?.sha256}>{hashText(manifest.vina?.sha256)}</dd></div>
            <div><dt>Payload SHA256</dt><dd title={manifest.maps?.payload_sha256}>{hashText(manifest.maps?.payload_sha256)}</dd></div>
            <div><dt>Prefix</dt><dd>{status.maps_prefix || manifest.maps?.prefix || "未记录"}</dd></div>
          </dl>
        </AdvancedDetails>
      ) : null}

      {disabled && disabledReason ? (
        <p className="vina-maps-disabled-reason">{disabledReason}</p>
      ) : null}
      {message ? (
        <p className="run-inline-message" role={rawError ? "alert" : "status"}>{message}</p>
      ) : null}
      {rawError ? (
        <AdvancedDetails summary="查看诊断"><pre>{rawError}</pre></AdvancedDetails>
      ) : null}

      <OperationLoadingDialog
        open={busyAction === "generate" || busyAction === "import"}
        title={busyAction === "generate" ? "正在生成 Vina maps" : "正在导入 Vina maps"}
        message={
          busyAction === "generate"
            ? "正在按当前受体、Box 与评分函数生成并校验网格。"
            : "正在复制 maps 并核对 manifest、哈希与运行条件。"
        }
        detail="完成后会自动更新网格来源与运行检查。"
      />
    </section>
  );
}
