import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { mapsProjectContextKey } from "../utils/mapsContext";
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
  useLanguage();
  const [status, setStatus] = useState<VinaMapsStatusResponse | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction>("");
  const [importKind, setImportKind] = useState<ImportKind>("dockstart_manifest");
  const [sourcePath, setSourcePath] = useState("");
  const [confirmations, setConfirmations] =
    useState<RawMapsConfirmations>(emptyConfirmations);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const loadRequestRef = useRef(0);
  const mapsContextKey = useMemo(() => mapsProjectContextKey(project), [project]);

  const load = useCallback(async () => {
    const requestId = ++loadRequestRef.current;
    setBusyAction((current) => current || "load");
    try {
      const raw = await invoke<string>("get_vina_maps_status", {
        projectDir: project.project_dir,
      });
      const response = JSON.parse(raw) as VinaMapsStatusResponse;
      if (requestId !== loadRequestRef.current) return;
      setStatus(response);
      if (!response.ok) {
        const error = responseError(response);
        setMessage(error.message);
        setRawError(error.detail);
      }
    } catch (error) {
      if (requestId !== loadRequestRef.current) return;
      setMessage("无法读取 Vina maps 状态。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      if (requestId === loadRequestRef.current) {
        setBusyAction((current) => (current === "load" ? "" : current));
      }
    }
  }, [mapsContextKey, project.project_dir]);

  useEffect(() => {
    setStatus(null);
    setSourcePath("");
    setImportKind("dockstart_manifest");
    setConfirmations(emptyConfirmations);
    setMessage("");
    setRawError("");
    void load();
    return () => {
      loadRequestRef.current += 1;
    };
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
          <span className="run-cockpit-kicker">{translate("Vina / Vinardo 网格")}</span>
          <h3 id="vina-maps-title">{translate("网格来源")}</h3>
        </div>
        <StatusBadge
          tone={mapsActive ? (status?.ready ? "ok" : "warning") : "info"}
        >
          {translate(mapsActive
            ? status?.ready
              ? "已保存 maps"
              : "maps 不可用"
            : "实时计算")}
        </StatusBadge>
      </header>

      <div className="vina-maps-source-switch" role="group" aria-label={translate("Vina 网格来源")}>
        <button
          type="button"
          className={!mapsActive ? "active" : ""}
          aria-pressed={!mapsActive}
          disabled={disabled || isBusy}
          onClick={() => void setMode("receptor")}
        >
          <Database aria-hidden="true" size={17} />
          <span><strong>{translate("实时计算")}</strong><small>{translate("由受体与当前 Box 建立")}</small></span>
        </button>
        <button
          type="button"
          className={mapsActive ? "active" : ""}
          aria-pressed={mapsActive}
          disabled={disabled || isBusy || !canActivateSaved}
          onClick={() => void setMode("precomputed_maps")}
          title={translate(!canActivateSaved ? "需先生成或导入一组可用 maps" : undefined)}
        >
          <GridFour aria-hidden="true" size={17} />
          <span><strong>{translate("已保存 maps")}</strong><small>{translate("读取已校验的网格文件")}</small></span>
        </button>
      </div>

      {translate(mapsActive ? (
        <div className={`vina-maps-status ${status?.ready ? "ready" : "blocked"}`}>
          {status?.ready
            ? <CheckCircle aria-hidden="true" size={21} weight="fill" />
            : <WarningCircle aria-hidden="true" size={21} weight="fill" />}
          <div>
            <strong>
              {translate(status?.ready
                ? `${status.map_set_id || "当前 maps"} 可用于运行`
                : "当前 maps 未通过运行检查")}
            </strong>
            <p>
              {translate(status?.ready
                ? `${currentScoring === "vinardo" ? "Vinardo" : "Vina"} · ${mapCount} 个 map 文件 · ${gridSummary.spacing}`
                : status?.issues?.[0] || status?.message || "请重新生成或导入 maps。")}
            </p>
          </div>
        </div>
      ) : (
        <div className="vina-maps-runtime-summary">
          <Database aria-hidden="true" size={19} />
          <div>
            <strong>{translate("运行时计算网格")}</strong>
            <p>{translate("Vina 将使用当前受体、Box 与 ")}{translate(currentScoring === "vinardo" ? "Vinardo" : "Vina")}{translate(" 评分函数。")}</p>
          </div>
        </div>
      ))}

      {compatibilityIssues.length ? (
        <div className="vina-maps-compatibility" role="status">
          <WarningCircle aria-hidden="true" size={18} weight="fill" />
          <p>{translate("预计算 maps 首版仅用于刚性受体与全局对接：")}{translate(compatibilityIssues.join("；"))}。</p>
        </div>
      ) : null}

      <div className="vina-maps-actions-grid">
        <section className="vina-maps-action-card vina-maps-save-card">
          <div className="vina-maps-semantics" aria-label={translate("预计算 maps 运行限制")}>
            <div>
              <span>grid-only</span>
              <span>{translate("no-refine 等效")}</span>
              <span>{translate("刚性受体")}</span>
              <span>{translate("全局对接")}</span>
            </div>
            <p>{translate("已保存 maps 模式不实时读取受体计算网格，也不执行最终受体原子精修。")}</p>
          </div>
          <div className="vina-maps-save-action">
            <header>
              <GridFour aria-hidden="true" size={18} />
              <div><strong>{translate("保存当前网格")}</strong><small>{translate("当前受体 · Box · ")}{translate(currentScoring === "vinardo" ? "Vinardo" : "Vina")}</small></div>
            </header>
            <ActionButton
              variant="primary"
              disabled={disabled || isBusy || !mapsModeAllowed}
              onClick={() => void generate()}
            >{translate("生成并启用")}</ActionButton>
          </div>
        </section>

        <section
          className={`vina-maps-action-card vina-maps-import-card ${importKind === "raw_maps" ? "is-raw-import" : ""}`.trim()}
        >
          <header>
            <FileArrowDown aria-hidden="true" size={18} />
            <div><strong>{translate("导入 maps")}</strong><small>{translate("DockStart manifest 或外部 raw maps")}</small></div>
          </header>
          <div className="vina-maps-import-kind" role="group" aria-label={translate("maps 导入类型")}>
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
            >{translate("外部 raw maps")}</button>
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
              translate(importKind === "raw_maps"
                ? "选择 raw maps 文件夹"
                : "选择 DockStart maps manifest")
            }
            placeholder={
              translate(importKind === "raw_maps"
                ? "选择包含 *.map 的文件夹"
                : "选择 vina_maps_manifest.json")
            }
            ariaLabel={translate("待导入的 Vina maps 来源")}
          />

          {importKind === "raw_maps" ? (
            <div className="vina-maps-attestation">
              <div className="vina-maps-attestation-context">
                <div><span>{translate("受体")}</span><strong title={translate(context?.receptor?.source_sha256)}>{translate(hashText(context?.receptor?.source_sha256))}</strong></div>
                <div>
                  <span>{translate("Box 中心")}</span>
                  <strong>
                    {translate(numberText(contextBox?.center?.x))} / {translate(numberText(contextBox?.center?.y))} / {translate(numberText(contextBox?.center?.z))}
                  </strong>
                </div>
                <div>
                  <span>{translate("Box 尺寸")}</span>
                  <strong>
                    {translate(numberText(contextBox?.size?.x))} / {translate(numberText(contextBox?.size?.y))} / {translate(numberText(contextBox?.size?.z))}
                  </strong>
                </div>
                <div><span>{translate("评分来源")}</span><strong>{translate(currentScoring === "vinardo" ? "Vinardo" : "Vina")} · {translate(context?.vina?.version || "版本未知")}</strong></div>
              </div>
              <div className="vina-maps-attestation-checks">
                <label>
                  <input
                    type="checkbox"
                    checked={confirmations.receptor}
                    disabled={disabled || isBusy}
                    onChange={(event) => setConfirmations({ ...confirmations, receptor: event.target.checked })}
                  />{translate("raw maps 属于上方受体")}</label>
                <label>
                  <input
                    type="checkbox"
                    checked={confirmations.box}
                    disabled={disabled || isBusy}
                    onChange={(event) => setConfirmations({ ...confirmations, box: event.target.checked })}
                  />{translate("网格范围与上方 Box 一致")}</label>
                <label>
                  <input
                    type="checkbox"
                    checked={confirmations.scoring}
                    disabled={disabled || isBusy}
                    onChange={(event) => setConfirmations({ ...confirmations, scoring: event.target.checked })}
                  />{translate("评分函数与 Vina 来源一致")}</label>
              </div>
              {!rawImportOptions ? (
                <p className="vina-maps-attestation-note">{translate("三项确认及当前受体、Vina 哈希齐全后才能导入。")}</p>
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
          >{translate("导入、校验并启用")}</ActionButton>
        </section>
      </div>

      {translate(manifest && status?.manifest_file ? (
        <AdvancedDetails summary={translate("maps manifest 详情")} className="vina-maps-manifest">
          <dl>
            <div><dt>Manifest</dt><dd>{translate(status.manifest_file)}</dd></div>
            <div><dt>Map set</dt><dd>{translate(status.map_set_id || manifest.map_set_id || "未记录")}</dd></div>
            <div><dt>{translate("来源")}</dt><dd>{translate(manifest.source || "未记录")}</dd></div>
            <div><dt>{translate("评分函数")}</dt><dd>{translate(manifest.scoring_function || "未记录")}</dd></div>
            <div><dt>{translate("中心")}</dt><dd>{translate(gridSummary.center)}</dd></div>
            <div><dt>{translate("尺寸")}</dt><dd>{translate(gridSummary.size)}</dd></div>
            <div><dt>{translate("网格点")}</dt><dd>{translate(gridSummary.elements)}</dd></div>
            <div><dt>Maps</dt><dd>{mapCount}{translate(" 个 · ")}{translate(manifest.maps?.total_size_bytes ?? "—")} bytes</dd></div>
            <div><dt>{translate("受体 SHA256")}</dt><dd title={translate(manifest.receptor?.source_sha256)}>{translate(hashText(manifest.receptor?.source_sha256))}</dd></div>
            <div><dt>Vina SHA256</dt><dd title={translate(manifest.vina?.sha256)}>{translate(hashText(manifest.vina?.sha256))}</dd></div>
            <div><dt>Payload SHA256</dt><dd title={translate(manifest.maps?.payload_sha256)}>{translate(hashText(manifest.maps?.payload_sha256))}</dd></div>
            <div><dt>Prefix</dt><dd>{translate(status.maps_prefix || manifest.maps?.prefix || "未记录")}</dd></div>
          </dl>
        </AdvancedDetails>
      ) : null)}

      {disabled && disabledReason ? (
        <p className="vina-maps-disabled-reason">{translate(disabledReason)}</p>
      ) : null}
      {message ? (
        <p className="run-inline-message" role={rawError ? "alert" : "status"}>{translate(message)}</p>
      ) : null}
      {rawError ? (
        <AdvancedDetails summary={translate("查看诊断")}><pre>{rawError}</pre></AdvancedDetails>
      ) : null}

      <OperationLoadingDialog
        open={busyAction === "generate" || busyAction === "import"}
        title={translate(busyAction === "generate" ? "正在生成 Vina maps" : "正在导入 Vina maps")}
        message={
          translate(busyAction === "generate"
            ? "正在按当前受体、Box 与评分函数生成并校验网格。"
            : "正在复制 maps 并核对 manifest、哈希与运行条件。")
        }
        detail={translate("完成后会自动更新网格来源与运行检查。")}
      />
    </section>
  );
}
