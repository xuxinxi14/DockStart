import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowsOut,
  Crosshair,
  MagnifyingGlassMinus,
  MagnifyingGlassPlus,
  Pause,
  Play,
} from "@phosphor-icons/react";
import type {
  MultipleLigandPoseViewerResult,
  ViewerStructureResult,
} from "../types";
import { addOrientationAxes } from "./viewerSceneHelpers";
import {
  load3Dmol,
  structureFingerprint,
  type ThreeDmolModel,
  type ThreeDmolViewer,
} from "./threeDmolLoader";

type MultiLigandPosePreviewProps = {
  projectDir: string;
  runId: string;
  mode: number;
  focusRequest?: { mode: number; token: number } | null;
  className?: string;
};

type ModelRecord = {
  fingerprint: string;
  model: ThreeDmolModel;
};

function parsePose(rawPayload: string): MultipleLigandPoseViewerResult {
  return JSON.parse(rawPayload) as MultipleLigandPoseViewerResult;
}

function responseMessage(response: MultipleLigandPoseViewerResult, fallback: string): string {
  return response.error?.message?.trim() || response.message?.trim() || fallback;
}

function memberLabel(response: MultipleLigandPoseViewerResult | null, fallback: string): string {
  return response?.member?.display_name?.trim()
    || response?.member?.source_name?.trim()
    || fallback;
}

export default function MultiLigandPosePreview({
  projectDir,
  runId,
  mode,
  focusRequest = null,
  className = "",
}: MultiLigandPosePreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ThreeDmolViewer | null>(null);
  const viewerInitRef = useRef<Promise<ThreeDmolViewer | null> | null>(null);
  const receptorModelRef = useRef<ModelRecord | null>(null);
  const firstModelRef = useRef<ModelRecord | null>(null);
  const secondModelRef = useRef<ModelRecord | null>(null);
  const loadGenerationRef = useRef(0);
  const hasFitRef = useRef(false);
  const [first, setFirst] = useState<MultipleLigandPoseViewerResult | null>(null);
  const [second, setSecond] = useState<MultipleLigandPoseViewerResult | null>(null);
  const [message, setMessage] = useState(`正在加载联合构象 Mode ${mode}…`);
  const [messageTone, setMessageTone] = useState<"status" | "error">("status");
  const [showReceptor, setShowReceptor] = useState(true);
  const [showFirst, setShowFirst] = useState(true);
  const [showSecond, setShowSecond] = useState(true);
  const [showAxes, setShowAxes] = useState(true);
  const [isSpinning, setIsSpinning] = useState(false);

  const ensureViewer = useCallback(async () => {
    if (viewerRef.current) return viewerRef.current;
    if (!containerRef.current) return null;
    if (!viewerInitRef.current) {
      const container = containerRef.current;
      viewerInitRef.current = load3Dmol().then(($3Dmol) => {
        if (!container.isConnected) return null;
        const background = getComputedStyle(document.documentElement)
          .getPropertyValue("--ds-viewer-bg")
          .trim();
        viewerRef.current = $3Dmol.createViewer(container, {
          backgroundColor: background || "#061c31",
        });
        return viewerRef.current;
      });
    }
    return viewerInitRef.current;
  }, []);

  const replaceModel = useCallback((
    viewer: ThreeDmolViewer,
    current: ModelRecord | null,
    structure: ViewerStructureResult | null | undefined,
    identity: string,
    style: Parameters<ThreeDmolModel["setStyle"]>[1],
  ): ModelRecord | null => {
    const fingerprint = structure?.ok
      ? structureFingerprint(structure.content, structure.format, identity)
      : "";
    if (!fingerprint) {
      if (current) viewer.removeModel(current.model);
      return null;
    }
    if (current?.fingerprint === fingerprint) return current;
    if (current) viewer.removeModel(current.model);
    const model = viewer.addModel(structure!.content, structure!.format);
    model.setStyle({}, style);
    return { fingerprint, model };
  }, []);

  const renderScene = useCallback(async (fit = false) => {
    const viewer = await ensureViewer();
    if (!viewer) return;
    const previousView = fit
      ? null
      : (viewer as unknown as { getView?: () => unknown }).getView?.();
    const receptor = first?.receptor?.ok ? first.receptor : second?.receptor;

    receptorModelRef.current = replaceModel(
      viewer,
      receptorModelRef.current,
      receptor,
      `${runId}:receptor:${receptor?.relative_path || ""}`,
      {
        cartoon: { color: "spectrum", opacity: 0.7 },
        stick: { radius: 0.09, colorscheme: "Jmol" },
      },
    );
    firstModelRef.current = replaceModel(
      viewer,
      firstModelRef.current,
      first?.pose,
      `${runId}:${mode}:member:1:${first?.pose?.relative_path || ""}`,
      {
        stick: { radius: 0.25, colorscheme: "cyanCarbon" },
        sphere: { scale: 0.2, colorscheme: "cyanCarbon" },
      },
    );
    secondModelRef.current = replaceModel(
      viewer,
      secondModelRef.current,
      second?.pose,
      `${runId}:${mode}:member:2:${second?.pose?.relative_path || ""}`,
      {
        stick: { radius: 0.25, colorscheme: "orangeCarbon" },
        sphere: { scale: 0.2, colorscheme: "orangeCarbon" },
      },
    );

    if (showReceptor) receptorModelRef.current?.model.show();
    else receptorModelRef.current?.model.hide();
    if (showFirst) firstModelRef.current?.model.show();
    else firstModelRef.current?.model.hide();
    if (showSecond) secondModelRef.current?.model.show();
    else secondModelRef.current?.model.hide();

    viewer.removeAllShapes();
    viewer.removeAllLabels();
    if (showAxes) addOrientationAxes(viewer, null);
    if (previousView) {
      (viewer as unknown as { setView?: (view: unknown) => void }).setView?.(previousView);
    } else {
      viewer.zoomTo();
    }
    viewer.render();
  }, [
    ensureViewer,
    first,
    mode,
    replaceModel,
    runId,
    second,
    showAxes,
    showFirst,
    showReceptor,
    showSecond,
  ]);

  useEffect(() => {
    const generation = ++loadGenerationRef.current;
    hasFitRef.current = false;
    setFirst(null);
    setSecond(null);
    setMessage(`正在加载联合构象 Mode ${mode}…`);
    setMessageTone("status");
    void Promise.all([
      invoke<string>("load_multiple_ligand_pose", {
        projectDir,
        runId,
        mode,
        memberIndex: 1,
      }),
      invoke<string>("load_multiple_ligand_pose", {
        projectDir,
        runId,
        mode,
        memberIndex: 2,
      }),
    ]).then(([firstPayload, secondPayload]) => {
      if (generation !== loadGenerationRef.current) return;
      const firstResponse = parsePose(firstPayload);
      const secondResponse = parsePose(secondPayload);
      if (!firstResponse.ok) throw new Error(responseMessage(firstResponse, "无法加载第一个配体。"));
      if (!secondResponse.ok) throw new Error(responseMessage(secondResponse, "无法加载第二个配体。"));
      if (firstResponse.mode !== secondResponse.mode || firstResponse.mode !== mode) {
        throw new Error("两个配体返回的联合构象编号不一致。");
      }
      setFirst(firstResponse);
      setSecond(secondResponse);
      setMessage(`联合构象 Mode ${mode} 已加载；两个配体共享同一联合评分。`);
    }).catch((error) => {
      if (generation !== loadGenerationRef.current) return;
      setMessage(error instanceof Error ? error.message : "无法加载联合构象。");
      setMessageTone("error");
    });
    return () => {
      loadGenerationRef.current += 1;
    };
  }, [mode, projectDir, runId]);

  useEffect(() => {
    const ready = Boolean(first?.pose?.ok && second?.pose?.ok);
    if (!ready) return;
    const shouldFit = !hasFitRef.current;
    void renderScene(shouldFit).then(() => {
      if (shouldFit) hasFitRef.current = true;
    }).catch((error) => {
      setMessage(error instanceof Error ? error.message : "3D 场景渲染失败。");
      setMessageTone("error");
    });
  }, [first, renderScene, second]);

  useEffect(() => {
    if (!focusRequest || focusRequest.mode !== mode) return;
    const models = [
      ...(showFirst && firstModelRef.current ? [firstModelRef.current.model] : []),
      ...(showSecond && secondModelRef.current ? [secondModelRef.current.model] : []),
    ];
    if (!models.length) return;
    const viewer = viewerRef.current;
    if (!viewer) return;
    viewer.zoomTo({ model: models } as never, 320);
    viewer.render();
    setMessage(`已定位到联合构象 Mode ${mode} 的两个配体。`);
    setMessageTone("status");
  }, [focusRequest, mode, showFirst, showSecond]);

  useEffect(() => () => {
    const viewer = viewerRef.current as unknown as {
      spin?: (axis: string | boolean, speed?: number) => void;
    } | null;
    viewer?.spin?.(false);
    viewerRef.current?.clear();
    viewerRef.current = null;
    viewerInitRef.current = null;
    receptorModelRef.current = null;
    firstModelRef.current = null;
    secondModelRef.current = null;
    if (containerRef.current) containerRef.current.replaceChildren();
  }, []);

  const zoom = (factor: number) => {
    viewerRef.current?.zoom(factor);
    viewerRef.current?.render();
  };

  const toggleSpin = () => {
    const viewer = viewerRef.current as unknown as {
      spin?: (axis: string | boolean, speed?: number) => void;
    } | null;
    const next = !isSpinning;
    if (next) viewer?.spin?.("y", 0.7);
    else viewer?.spin?.(false);
    setIsSpinning(next);
  };

  const firstLabel = memberLabel(first, "配体 1");
  const secondLabel = memberLabel(second, "配体 2");
  const isBusy = messageTone !== "error" && (!first?.ok || !second?.ok);

  return (
    <div
      className={`run-preview pose-structure-preview multi-ligand-pose-preview ${className}`.trim()}
      aria-label={`联合构象 Mode ${mode} 3D 预览`}
      aria-busy={isBusy}
    >
      <div className="run-preview-toolbar" aria-label="3D 视图工具">
        <button type="button" onClick={() => zoom(1.18)} title="放大" aria-label="放大">
          <MagnifyingGlassPlus size={18} />
        </button>
        <button type="button" onClick={() => zoom(0.84)} title="缩小" aria-label="缩小">
          <MagnifyingGlassMinus size={18} />
        </button>
        <button type="button" onClick={() => void renderScene(true)} title="适应窗口" aria-label="适应窗口">
          <ArrowsOut size={18} />
        </button>
        <button type="button" onClick={toggleSpin} title={isSpinning ? "停止旋转" : "自动旋转"} aria-label="旋转">
          {isSpinning ? <Pause size={18} /> : <Play size={18} />}
        </button>
        <button
          type="button"
          className={showAxes ? "is-active" : ""}
          onClick={() => setShowAxes((current) => !current)}
          title={showAxes ? "隐藏坐标轴" : "显示坐标轴"}
          aria-label={showAxes ? "隐藏坐标轴" : "显示坐标轴"}
          aria-pressed={showAxes}
        >
          <Crosshair size={18} />
        </button>
      </div>
      <div
        ref={containerRef}
        className="run-preview-canvas"
        style={{ height: "100%" }}
        role="img"
        tabIndex={0}
        aria-label={`联合构象 Mode ${mode}。${firstLabel} 为青色，${secondLabel} 为橙色。`}
      />
      <div className="run-preview-legend">
        <button
          type="button"
          className={`legend-toggle-btn ${showReceptor ? "is-active" : "is-inactive"}`}
          onClick={() => setShowReceptor((current) => !current)}
          aria-pressed={showReceptor}
        >
          <i className={`run-preview-dot receptor ${showReceptor ? "" : "muted"}`} />
          受体
        </button>
        <button
          type="button"
          className={`legend-toggle-btn ${showFirst ? "is-active" : "is-inactive"}`}
          onClick={() => setShowFirst((current) => !current)}
          aria-pressed={showFirst}
        >
          <i
            className={`run-preview-dot ${showFirst ? "" : "muted"}`}
            style={{ backgroundColor: "#45d7e8" }}
          />
          {firstLabel}
        </button>
        <button
          type="button"
          className={`legend-toggle-btn ${showSecond ? "is-active" : "is-inactive"}`}
          onClick={() => setShowSecond((current) => !current)}
          aria-pressed={showSecond}
        >
          <i
            className={`run-preview-dot ${showSecond ? "" : "muted"}`}
            style={{ backgroundColor: "#ff9b52" }}
          />
          {secondLabel}
        </button>
        <strong
          aria-live={messageTone === "error" ? "assertive" : "polite"}
          role={messageTone === "error" ? "alert" : "status"}
        >
          {message}
        </strong>
      </div>
    </div>
  );
}
