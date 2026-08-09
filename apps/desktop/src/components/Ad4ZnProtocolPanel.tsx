import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Check, CheckCircle, WarningCircle } from "@phosphor-icons/react";
import type {
  Ad4ZnConfirmationKey,
  Ad4ZnStatusResponse,
  AutoGridMapsStatusResponse,
  DockStartProject,
} from "../types";
import { mapsProjectContextKey } from "../utils/mapsContext";
import ActionButton from "./ActionButton";
import AdvancedDetails from "./AdvancedDetails";
import OperationLoadingDialog from "./OperationLoadingDialog";
import PathInput from "./PathInput";
import StatusBadge from "./StatusBadge";

type Ad4ZnProtocolPanelProps = {
  project: DockStartProject;
  disabled?: boolean;
  disabledReason?: string;
  mapsBusy?: boolean;
  refreshToken?: number;
  onBusyChange?: (busy: boolean) => void;
  onProjectChange: (project: DockStartProject) => void;
  onGenerateMaps: () => Promise<void>;
  onProtocolStatus: (status: Ad4ZnStatusResponse | null, mapsStatus: AutoGridMapsStatusResponse | null) => void;
  onStatusChange?: () => void;
};

type BusyAction = "" | "load" | "review" | "prepare" | "parameter";

const confirmationDefinitions: Array<{
  key: Ad4ZnConfirmationKey;
  label: string;
}> = [
  { key: "target", label: "该 Zn 位点是本次对接关注的金属位点" },
  { key: "coordination", label: "已核对配位原子与配位残基" },
  { key: "protonation", label: "已核对配位残基的质子化状态" },
  { key: "water", label: "已决定配位水的保留或移除" },
  { key: "cofactor", label: "已核对辅因子及其处理方式" },
  { key: "tz_pseudoatom", label: "了解 TZ 是 AD4Zn 评分使用的伪原子" },
  { key: "zinc_only_scope", label: "确认该协议只用于单核 Zn，不用于多核位点或其他金属参数化" },
];

const emptyConfirmations = (): Record<Ad4ZnConfirmationKey, boolean> => ({
  target: false,
  coordination: false,
  protonation: false,
  water: false,
  cofactor: false,
  tz_pseudoatom: false,
  zinc_only_scope: false,
});

function parseVersion(value: string): [number, number, number] | null {
  const match = value.match(/(\d+)\.(\d+)\.(\d+)/);
  if (!match) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

function versionAtLeast(value: string, minimum: [number, number, number]): boolean {
  const parsed = parseVersion(value);
  if (!parsed) return false;
  for (let index = 0; index < minimum.length; index += 1) {
    if (parsed[index] > minimum[index]) return true;
    if (parsed[index] < minimum[index]) return false;
  }
  return true;
}

export function getAd4ZnWorkflowReadiness(
  status: Ad4ZnStatusResponse | null,
  mapsStatus: AutoGridMapsStatusResponse | null,
) {
  const autoGridVersion = mapsStatus?.tool?.version || "";
  const mapsPipelineConnected = status?.compatibility?.maps_pipeline_connected === true;
  const autoGridCompatible = mapsPipelineConnected
    && mapsStatus?.tool?.status === "ok"
    && mapsStatus.tool.ad4zn_compatible === true
    && versionAtLeast(autoGridVersion, [4, 2, 7]);
  const protocolReady = Boolean(status?.preparation_ready || status?.ready);
  const mapsReady = Boolean(
    mapsStatus?.ready
    && mapsStatus.manifest?.protocol_id === "ad4zn_beta"
    && mapsStatus.tool?.ad4zn_compatible === true,
  );
  return {
    autoGridVersion,
    mapsPipelineConnected,
    autoGridCompatible,
    protocolReady,
    mapsReady,
    workflowReady: protocolReady && mapsReady && autoGridCompatible,
  };
}

function responseError(payload: {
  error?: { message?: string; raw_error?: string; suggestion?: string } | null;
  message?: string;
}): { message: string; detail: string } {
  return {
    message: payload.error?.message || payload.message || "操作失败。",
    detail: [payload.error?.raw_error, payload.error?.suggestion].filter(Boolean).join("\n"),
  };
}

function atomResidueLabel(atom: {
  residue_name: string;
  chain: string;
  residue_number: string | number;
  insertion_code: string;
}): string {
  const chain = atom.chain || "—";
  const insertion = atom.insertion_code || "";
  return `${atom.residue_name || "UNK"} ${chain}:${atom.residue_number}${insertion}`;
}

function coordinateLabel(coordinate: { x: number; y: number; z: number } | null | undefined): string {
  if (!coordinate) return "—";
  return `${coordinate.x.toFixed(3)}, ${coordinate.y.toFixed(3)}, ${coordinate.z.toFixed(3)}`;
}

export default function Ad4ZnProtocolPanel({
  project,
  disabled = false,
  disabledReason = "",
  mapsBusy = false,
  refreshToken = 0,
  onBusyChange,
  onProjectChange,
  onGenerateMaps,
  onProtocolStatus,
  onStatusChange,
}: Ad4ZnProtocolPanelProps) {
  const [status, setStatus] = useState<Ad4ZnStatusResponse | null>(null);
  const [mapsStatus, setMapsStatus] = useState<AutoGridMapsStatusResponse | null>(null);
  const [selectedSiteId, setSelectedSiteId] = useState("");
  const [confirmations, setConfirmations] = useState<Record<Ad4ZnConfirmationKey, boolean>>(emptyConfirmations);
  const [parameterFile, setParameterFile] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [busyAction, setBusyAction] = useState<BusyAction>("");
  const loadRequestRef = useRef(0);
  const mapsContextKey = useMemo(() => mapsProjectContextKey(project), [project]);

  const applyStatus = useCallback((response: Ad4ZnStatusResponse) => {
    setStatus(response);
    const nextSiteId = response.selected_site?.site_id
      || response.selected_site_id
      || response.review?.selected_site_id
      || response.sites?.[0]?.site_id
      || "";
    setSelectedSiteId(nextSiteId);
    const saved = response.review?.valid ? response.review.confirmations ?? {} : {};
    setConfirmations({
      ...emptyConfirmations(),
      ...saved,
    });
  }, []);

  const load = useCallback(async (preserveFeedback = false) => {
    const requestId = ++loadRequestRef.current;
    setBusyAction((current) => current || "load");
    if (!preserveFeedback) {
      setMessage("");
      setRawError("");
    }
    try {
      const [protocolResult, mapsResult] = await Promise.allSettled([
        invoke<string>("get_ad4zn_status", { projectDir: project.project_dir }),
        invoke<string>("get_autogrid_maps_status", { projectDir: project.project_dir }),
      ]);
      if (requestId !== loadRequestRef.current) return;

      let nextProtocolStatus: Ad4ZnStatusResponse | null = null;
      let nextMapsStatus: AutoGridMapsStatusResponse | null = null;
      const diagnostics: Array<{ message: string; detail: string }> = [];

      if (protocolResult.status === "fulfilled") {
        nextProtocolStatus = JSON.parse(protocolResult.value) as Ad4ZnStatusResponse;
        applyStatus(nextProtocolStatus);
        if (!nextProtocolStatus.ok) {
          diagnostics.push(responseError(nextProtocolStatus));
        }
      } else {
        diagnostics.push({
          message: "无法读取 AD4Zn 状态。",
          detail: protocolResult.reason instanceof Error ? protocolResult.reason.message : String(protocolResult.reason),
        });
      }

      if (mapsResult.status === "fulfilled") {
        nextMapsStatus = JSON.parse(mapsResult.value) as AutoGridMapsStatusResponse;
        setMapsStatus(nextMapsStatus);
        if (!nextMapsStatus.ok) {
          diagnostics.push(responseError(nextMapsStatus));
        }
      } else {
        setMapsStatus(null);
        diagnostics.push({
          message: "无法读取 AD4Zn maps 状态。",
          detail: mapsResult.reason instanceof Error ? mapsResult.reason.message : String(mapsResult.reason),
        });
      }

      if (diagnostics.length) {
        setMessage(diagnostics.map((item) => item.message).join(" "));
        setRawError(diagnostics.map((item) => item.detail).filter(Boolean).join("\n\n"));
      }

      onProtocolStatus(nextProtocolStatus, nextMapsStatus);
    } catch (error) {
      if (requestId !== loadRequestRef.current) return;
      setMessage("无法读取 AD4Zn 协议状态。");
      setRawError(error instanceof Error ? error.message : String(error));
      onProtocolStatus(null, null);
    } finally {
      if (requestId === loadRequestRef.current) {
        setBusyAction((current) => (current === "load" ? "" : current));
      }
    }
  }, [applyStatus, mapsContextKey, onProtocolStatus, project.project_dir]);

  useEffect(() => {
    void load();
    return () => {
      loadRequestRef.current += 1;
    };
  }, [load, refreshToken]);

  useEffect(() => {
    onBusyChange?.(Boolean(busyAction));
  }, [busyAction, onBusyChange]);

  useEffect(
    () => () => {
      onBusyChange?.(false);
    },
    [onBusyChange],
  );

  const selectedSite = useMemo(
    () => status?.sites?.find((site) => site.site_id === selectedSiteId) ?? null,
    [selectedSiteId, status?.sites],
  );
  const coordinationRows = useMemo(
    () => selectedSite?.neighbors.filter((neighbor) => neighbor.coordinating || neighbor.eligible) ?? [],
    [selectedSite],
  );
  const requiredConfirmations = status?.required_confirmations?.length
    ? status.required_confirmations
    : confirmationDefinitions.map((item) => item.key);
  const allConfirmed = requiredConfirmations.every((key) => confirmations[key]);
  const {
    autoGridVersion,
    autoGridCompatible,
    protocolReady,
    mapsReady,
    workflowReady,
  } = getAd4ZnWorkflowReadiness(status, mapsStatus);
  const isBusy = Boolean(busyAction) || mapsBusy;
  const reviewStateLabel = status?.review?.valid
    ? "已保存"
    : status?.review?.recorded ? "需重新确认" : "待确认";
  const preparedReceptorStateLabel = status?.prepared_receptor?.valid
    ? "已校验"
    : status?.prepared_receptor?.recorded ? "校验失效" : "未生成";
  const parameterFileStateLabel = status?.parameter_file?.valid
    ? "已校验"
    : status?.parameter_file?.recorded ? "校验失效" : "未设置";
  const mapsStateLabel = mapsReady
    ? "已就绪"
    : mapsStatus?.ok === false ? "校验失败" : "未生成";

  const progressItems = [
    { key: "review", label: "Zn 复核", ready: Boolean(status?.review?.valid) },
    { key: "tz", label: "TZ 受体", ready: Boolean(status?.prepared_receptor?.valid) },
    { key: "parameter", label: "参数", ready: Boolean(status?.parameter_file?.valid) },
    { key: "maps", label: "maps", ready: mapsReady },
  ];

  const runProtocolAction = async (
    action: Exclude<BusyAction, "" | "load">,
    command: string,
    args: Record<string, unknown>,
    fallbackMessage: string,
  ): Promise<boolean> => {
    if (disabled || isBusy) return false;
    setBusyAction(action);
    setMessage("");
    setRawError("");
    try {
      const raw = await invoke<string>(command, args);
      const response = JSON.parse(raw) as Ad4ZnStatusResponse;
      if (!response.ok) {
        const error = responseError(response);
        setMessage(error.message);
        setRawError(error.detail);
        return false;
      }
      applyStatus(response);
      if (response.project) onProjectChange(response.project);
      setMessage(response.message || fallbackMessage);
      await load(true);
      onStatusChange?.();
      return true;
    } catch (error) {
      setMessage(fallbackMessage.replace(/已.*$/, "失败。"));
      setRawError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setBusyAction("");
    }
  };

  const saveReview = async () => {
    if (!selectedSiteId || !allConfirmed) return;
    await runProtocolAction(
      "review",
      "save_ad4zn_review",
      {
        projectDir: project.project_dir,
        reviewJson: JSON.stringify({
          selected_site_id: selectedSiteId,
          confirmations,
        }),
      },
      "Zn 位点复核已保存。",
    );
  };

  const prepareReceptor = async () => {
    await runProtocolAction(
      "prepare",
      "prepare_ad4zn_receptor",
      {
        projectDir: project.project_dir,
        optionsJson: JSON.stringify({}),
      },
      "TZ 受体已生成并校验。",
    );
  };

  const saveParameterFile = async () => {
    if (!parameterFile.trim()) return;
    const saved = await runProtocolAction(
      "parameter",
      "set_ad4zn_parameter_file",
      {
        projectDir: project.project_dir,
        parameterFile: parameterFile.trim(),
      },
      "AD4Zn 参数文件已复制并校验。",
    );
    if (saved) setParameterFile("");
  };

  const generateMaps = async () => {
    if (!status?.step_readiness?.run || !autoGridCompatible || disabled || isBusy) return;
    setMessage("");
    setRawError("");
    await onGenerateMaps();
  };

  const loadingCopy = busyAction === "review"
    ? { title: "正在保存 Zn 复核", message: "正在绑定受体、Zn 位点与确认记录。" }
    : busyAction === "prepare"
      ? { title: "正在生成 TZ 受体", message: "正在生成伪原子并校验 TZ 与 Zn 的几何关系。" }
      : { title: "正在校验 AD4Zn 参数", message: "正在核对关键参数与 GPL 声明，并复制到项目。" };

  return (
    <div className="ad4zn-workspace" aria-busy={isBusy}>
      <div className={`ad4zn-summary ${workflowReady ? "ready" : "blocked"}`}>
        {workflowReady
          ? <CheckCircle aria-hidden="true" size={22} weight="fill" />
          : <WarningCircle aria-hidden="true" size={22} weight="fill" />}
        <div>
          <strong>{workflowReady ? "AutoDock4Zn beta 已就绪" : "AutoDock4Zn beta 尚未就绪"}</strong>
          <p>{workflowReady ? `${mapsStatus?.map_set_id || "当前 maps"} 可用于运行` : "完成 Zn 复核、TZ 受体、参数文件与 maps。"}</p>
        </div>
        <StatusBadge tone={autoGridCompatible ? "ok" : "warning"}>
          {autoGridCompatible ? `AutoGrid4 ${autoGridVersion}` : "需要 AutoGrid4 4.2.7+"}
        </StatusBadge>
      </div>

      <ol className="ad4zn-progress" aria-label="AD4Zn 准备进度">
        {progressItems.map((item, index) => (
          <li className={item.ready ? "ready" : ""} key={item.key}>
            <span aria-hidden="true">
              {item.ready ? <Check size={12} weight="bold" /> : index + 1}
            </span>
            <strong>{item.label}</strong>
            <small>{item.ready ? "完成" : "待处理"}</small>
          </li>
        ))}
      </ol>

      {status?.issues?.length ? (
        <div className="ad4zn-issues" role={!protocolReady ? "alert" : "status"}>
          {status.issues.map((issue, index) => (
            <div key={issue.code || `issue-${index}`}>
              <strong>{issue.title}</strong>
              <p>{issue.message}</p>
              {issue.suggestion ? <small>{issue.suggestion}</small> : null}
            </div>
          ))}
        </div>
      ) : null}

      <section className="ad4zn-section" aria-labelledby="ad4zn-review-title">
        <div className="ad4zn-section-heading">
          <div>
            <span>步骤 1</span>
            <h3 id="ad4zn-review-title">Zn 位点复核</h3>
          </div>
          <StatusBadge tone={status?.review?.valid ? "ok" : "warning"}>
            {reviewStateLabel}
          </StatusBadge>
        </div>

        {status?.sites?.length ? (
          <>
            <label className="ad4zn-site-picker" htmlFor="ad4zn-site">
              <span>Zn 位点</span>
              <select
                id="ad4zn-site"
                value={selectedSiteId}
                disabled={disabled || isBusy}
                onChange={(event) => {
                  setSelectedSiteId(event.target.value);
                  setConfirmations(emptyConfirmations());
                }}
              >
                {status.sites.map((site) => (
                  <option key={site.site_id} value={site.site_id}>
                    {`${atomResidueLabel(site.zn)} · ${site.zn.name || "ZN"} · serial ${site.zn.serial}`}
                  </option>
                ))}
              </select>
            </label>

            {selectedSite ? (
              <div className="ad4zn-site-details">
                <dl className="ad4zn-site-summary">
                  <div><dt>Zn 原子</dt><dd>{`${selectedSite.zn.name || "ZN"} · serial ${selectedSite.zn.serial}`}</dd></div>
                  <div><dt>残基</dt><dd>{atomResidueLabel(selectedSite.zn)}</dd></div>
                  <div><dt>坐标（Å）</dt><dd>{coordinateLabel(selectedSite.zn.coordinate)}</dd></div>
                  <div><dt>配位数</dt><dd>{selectedSite.coordination_number}</dd></div>
                  <div><dt>自动准备</dt><dd>{selectedSite.can_generate ? "支持" : "需要人工处理"}</dd></div>
                  <div><dt>TZ 候选距离</dt><dd>{selectedSite.tz_candidate ? `${selectedSite.tz_candidate.distance.toFixed(2)} Å` : "不可生成"}</dd></div>
                  <div><dt>TZ 候选坐标</dt><dd>{coordinateLabel(selectedSite.tz_candidate?.coordinate)}</dd></div>
                </dl>

                <div className="ad4zn-coordination-table">
                  <table aria-label="所选 Zn 位点的邻近与配位原子">
                    <thead>
                      <tr><th>原子</th><th>残基</th><th>距离（Å）</th><th>判定</th></tr>
                    </thead>
                    <tbody>
                      {coordinationRows.length ? coordinationRows.map((neighbor) => (
                        <tr key={neighbor.atom.atom_id}>
                          <td>{`${neighbor.atom.name} · ${neighbor.atom.serial}`}</td>
                          <td>{atomResidueLabel(neighbor.atom)}</td>
                          <td>{neighbor.distance.toFixed(3)}</td>
                          <td>{neighbor.coordinating
                            ? "配位"
                            : neighbor.excluded_by_connectivity
                              ? "邻接排除"
                              : neighbor.eligible ? "候选" : "邻近"}</td>
                        </tr>
                      )) : (
                        <tr><td colSpan={4}>未识别到配位原子或候选原子。</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}

            {selectedSite && !selectedSite.can_generate ? (
              <p className="ad4zn-site-warning" role="alert">
                {selectedSite.geometry_message || "当前位点不满足自动生成 TZ 的条件。"}
              </p>
            ) : null}

            <fieldset className="ad4zn-confirmations">
              <legend>结构确认</legend>
              {confirmationDefinitions.map((item) => (
                <label key={item.key}>
                  <input
                    type="checkbox"
                    checked={confirmations[item.key]}
                    disabled={disabled || isBusy}
                    onChange={(event) => setConfirmations((current) => ({
                      ...current,
                      [item.key]: event.target.checked,
                    }))}
                  />
                  <span>{item.label}</span>
                </label>
              ))}
            </fieldset>

            <div className="ad4zn-section-actions">
              <ActionButton
                variant="primary"
                disabled={disabled || isBusy || !status?.step_readiness?.review || !selectedSite?.can_generate || !allConfirmed}
                onClick={() => void saveReview()}
              >
                保存 Zn 复核
              </ActionButton>
              {!allConfirmed ? <small>完成全部确认后保存。</small> : null}
            </div>
          </>
        ) : (
          <p className="ad4zn-empty">当前受体未识别到可复核的 Zn 位点。</p>
        )}
      </section>

      <div className="ad4zn-preparation-grid">
        <section className="ad4zn-section" aria-labelledby="ad4zn-tz-title">
          <div className="ad4zn-section-heading">
            <div><span>步骤 2</span><h3 id="ad4zn-tz-title">TZ 受体</h3></div>
            <StatusBadge tone={status?.prepared_receptor?.valid ? "ok" : "warning"}>
              {preparedReceptorStateLabel}
            </StatusBadge>
          </div>
          <p>根据已保存的 Zn 位点生成带 TZ 伪原子的受体 PDBQT。</p>
          {status?.prepared_receptor?.relative_path ? (
            <code className="ad4zn-file-path">{status.prepared_receptor.relative_path}</code>
          ) : null}
          <ActionButton
            disabled={disabled || isBusy || !status?.step_readiness?.prepare}
            onClick={() => void prepareReceptor()}
          >
            {status?.prepared_receptor?.recorded ? "重新生成 TZ 受体" : "生成 TZ 受体"}
          </ActionButton>
        </section>

        <section className="ad4zn-section" aria-labelledby="ad4zn-parameter-title">
          <div className="ad4zn-section-heading">
            <div><span>步骤 3</span><h3 id="ad4zn-parameter-title">AD4Zn 参数</h3></div>
            <StatusBadge tone={status?.parameter_file?.valid ? "ok" : "warning"}>
              {parameterFileStateLabel}
            </StatusBadge>
          </div>
          <p>选择 AD4Zn 参数文件；DockStart 会核对 v1.2.7 关键参数与 GPL 声明，再复制到项目并记录校验值。</p>
          {status?.parameter_file?.relative_path ? (
            <code className="ad4zn-file-path">{status.parameter_file.relative_path}</code>
          ) : null}
          <label className="ad4zn-parameter-picker" htmlFor="ad4zn-parameter-file">
            <span>参数文件</span>
            <PathInput
              id="ad4zn-parameter-file"
              value={parameterFile}
              onChange={setParameterFile}
              disabled={disabled || isBusy}
              mode="file"
              filters={[{ name: "AD4Zn parameter", extensions: ["dat"] }]}
              title="选择 AD4Zn 参数文件"
              placeholder="选择 AD4Zn.dat"
              ariaLabel="AD4Zn 参数文件"
            />
          </label>
          <ActionButton
            disabled={disabled || isBusy || !parameterFile.trim()}
            onClick={() => void saveParameterFile()}
          >
            复制并校验参数
          </ActionButton>
        </section>
      </div>

      <section className="ad4zn-section ad4zn-maps-section" aria-labelledby="ad4zn-maps-title">
        <div className="ad4zn-section-heading">
          <div><span>步骤 4</span><h3 id="ad4zn-maps-title">AutoDock4Zn maps</h3></div>
          <StatusBadge tone={mapsReady ? "ok" : "warning"}>{mapsStateLabel}</StatusBadge>
        </div>
        <p>使用 TZ 受体、AD4Zn 参数与当前项目 Box 生成 maps。</p>
        {!mapsReady ? (
          <p className="ad4zn-map-status-note">
            {mapsStatus?.issues?.[0]
              || mapsStatus?.error?.message
              || mapsStatus?.message
              || "正在读取 maps 状态。"}
          </p>
        ) : null}
        {mapsReady && mapsStatus?.map_set_id ? (
          <code className="ad4zn-file-path">{mapsStatus.map_set_id}</code>
        ) : null}
        <div className="ad4zn-section-actions">
          <ActionButton
            variant="primary"
            disabled={disabled || isBusy || !status?.step_readiness?.run || !autoGridCompatible}
            onClick={() => void generateMaps()}
          >
            {mapsReady ? "重新生成 AD4Zn maps" : "生成 AD4Zn maps"}
          </ActionButton>
          {!autoGridCompatible ? <small>需要 AutoGrid4 4.2.7 或更高版本。</small> : null}
        </div>
      </section>

      {disabled && disabledReason ? <p className="ad4-disabled-reason">{disabledReason}</p> : null}
      {message ? <p className="run-inline-message" role={rawError ? "alert" : "status"}>{message}</p> : null}
      {rawError ? <AdvancedDetails summary="查看诊断"><pre>{rawError}</pre></AdvancedDetails> : null}

      <OperationLoadingDialog
        open={busyAction === "review" || busyAction === "prepare" || busyAction === "parameter"}
        title={loadingCopy.title}
        message={loadingCopy.message}
        detail="完成后会自动刷新协议状态。"
      />
    </div>
  );
}
