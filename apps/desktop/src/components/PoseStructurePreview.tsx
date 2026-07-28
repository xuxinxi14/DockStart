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
  LocalPoseKind,
  LocalPosePairViewerResult,
  LocalPoseView,
  ScreeningPoseViewerResult,
  ViewerStructureResult,
} from "../types";
import { addOrientationAxes } from "./viewerSceneHelpers";
import {
  load3Dmol,
  structureFingerprint,
  type ThreeDmolModel,
  type ThreeDmolViewer,
} from "./threeDmolLoader";

type PoseStructurePreviewProps = {
  projectDir: string;
  runId?: string;
  screeningItemId?: string;
  screeningArchiveId?: string;
  mode: number;
  poseKind?: LocalPoseKind;
  poseLabel?: string;
  localPoseView?: LocalPoseView;
  onLocalPoseViewChange?: (view: LocalPoseView) => void;
  focusRequest?: { mode: number; token: number } | null;
  refreshKey?: number;
  className?: string;
};

type ModelRecord = {
  fingerprint: string;
  model: ThreeDmolModel;
};

function parseStructure(rawPayload: string): ViewerStructureResult {
  return JSON.parse(rawPayload) as ViewerStructureResult;
}

function parseLocalPosePair(rawPayload: string): LocalPosePairViewerResult {
  return JSON.parse(rawPayload) as LocalPosePairViewerResult;
}

function parseScreeningPose(rawPayload: string): ScreeningPoseViewerResult {
  return JSON.parse(rawPayload) as ScreeningPoseViewerResult;
}

function responseErrorMessage(
  response: { message?: string; error?: { message?: string } | null },
  fallback: string,
): string {
  return response.error?.message?.trim() || response.message?.trim() || fallback;
}

function localViewLabel(view: LocalPoseView): string {
  if (view === "input") return "输入姿势";
  if (view === "optimized") return "优化后姿势";
  return "叠合姿势";
}

export default function PoseStructurePreview({
  projectDir,
  runId = "",
  screeningItemId = "",
  screeningArchiveId = "",
  mode,
  poseKind,
  poseLabel,
  localPoseView,
  onLocalPoseViewChange,
  focusRequest = null,
  refreshKey = 0,
  className = "",
}: PoseStructurePreviewProps) {
  const isLocalPosePair = localPoseView !== undefined;
  const isScreeningPose = Boolean(screeningItemId);
  const sourceIdentity = isScreeningPose
    ? `screening:${screeningArchiveId ? `archive:${screeningArchiveId}` : "active"}:${screeningItemId}`
    : `run:${runId}`;
  const displayPoseLabel = poseLabel?.trim() || `Mode ${mode}`;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ThreeDmolViewer | null>(null);
  const viewerInitRef = useRef<Promise<ThreeDmolViewer | null> | null>(null);
  const receptorModelRef = useRef<ModelRecord | null>(null);
  const poseModelRef = useRef<ModelRecord | null>(null);
  const inputPoseModelRef = useRef<ModelRecord | null>(null);
  const optimizedPoseModelRef = useRef<ModelRecord | null>(null);
  const inputFlexModelRef = useRef<ModelRecord | null>(null);
  const optimizedFlexModelRef = useRef<ModelRecord | null>(null);
  const hasFitRef = useRef(false);
  const identityRef = useRef("");
  const sceneGenerationRef = useRef(0);
  const receptorLoadGenerationRef = useRef(0);
  const poseLoadGenerationRef = useRef(0);
  const pairLoadGenerationRef = useRef(0);
  const screeningLoadGenerationRef = useRef(0);
  const loadedPoseModeRef = useRef<number | null>(null);
  const [receptor, setReceptor] = useState<ViewerStructureResult | null>(null);
  const [pose, setPose] = useState<ViewerStructureResult | null>(null);
  const [localPair, setLocalPair] = useState<LocalPosePairViewerResult | null>(null);
  const [pairLoadDone, setPairLoadDone] = useState(false);
  const [message, setMessage] = useState(
    isLocalPosePair ? "正在加载局部优化前后姿势…" : `正在加载 ${displayPoseLabel}…`,
  );
  const [messageTone, setMessageTone] = useState<"status" | "error">("status");
  const [isSpinning, setIsSpinning] = useState(false);
  const [showReceptor, setShowReceptor] = useState(true);
  const [showPose, setShowPose] = useState(true);
  const [showAxes, setShowAxes] = useState(true);

  const showInputPose = localPoseView === "input" || localPoseView === "overlay";
  const showOptimizedPose = localPoseView === "optimized" || localPoseView === "overlay";
  const sceneReceptor = isLocalPosePair ? localPair?.receptor ?? null : receptor;
  const isBusy = isLocalPosePair
    ? !pairLoadDone
    : !(pose?.ok || pose?.error || messageTone === "error");

  const ensureViewer = useCallback(async () => {
    if (viewerRef.current) return viewerRef.current;
    if (!containerRef.current) return null;
    if (!viewerInitRef.current) {
      const container = containerRef.current;
      viewerInitRef.current = load3Dmol().then(($3Dmol) => {
        if (!container.isConnected) return null;
        const background = getComputedStyle(document.documentElement).getPropertyValue("--ds-viewer-bg").trim();
        viewerRef.current = $3Dmol.createViewer(container, { backgroundColor: background || "#061c31" });
        return viewerRef.current;
      });
    }
    return viewerInitRef.current;
  }, []);

  const replaceModel = useCallback((
    viewer: ThreeDmolViewer,
    current: ModelRecord | null,
    structure: ViewerStructureResult | null,
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
    const sceneGeneration = sceneGenerationRef.current;
    const viewer = await ensureViewer();
    if (!viewer || sceneGeneration !== sceneGenerationRef.current) return;
    const previousView = fit ? null : (viewer as unknown as { getView?: () => unknown }).getView?.();

    receptorModelRef.current = replaceModel(
      viewer,
      receptorModelRef.current,
      sceneReceptor,
      `${sceneReceptor?.relative_path || ""}:${refreshKey}`,
      {
        cartoon: { color: "spectrum", opacity: 0.74 },
        stick: { radius: 0.1, colorscheme: "Jmol" },
      },
    );
    if (showReceptor) receptorModelRef.current?.model.show();
    else receptorModelRef.current?.model.hide();

    if (isLocalPosePair) {
      poseModelRef.current = replaceModel(viewer, poseModelRef.current, null, "", {});
      inputPoseModelRef.current = replaceModel(
        viewer,
        inputPoseModelRef.current,
        localPair?.input ?? null,
        `${runId}:input:${localPair?.input?.relative_path || ""}:${refreshKey}`,
        { stick: { radius: 0.18, colorscheme: "cyanCarbon" } },
      );
      optimizedPoseModelRef.current = replaceModel(
        viewer,
        optimizedPoseModelRef.current,
        localPair?.optimized ?? null,
        `${runId}:optimized:${localPair?.optimized?.relative_path || ""}:${refreshKey}`,
        {
          stick: { radius: 0.26, colorscheme: "orangeCarbon" },
          sphere: { scale: 0.2, colorscheme: "orangeCarbon" },
        },
      );
      inputFlexModelRef.current = replaceModel(
        viewer,
        inputFlexModelRef.current,
        localPair?.flex_receptor_input ?? null,
        `${runId}:flex-input:${localPair?.flex_receptor_input?.relative_path || ""}:${refreshKey}`,
        { stick: { radius: 0.14, colorscheme: "cyanCarbon" } },
      );
      optimizedFlexModelRef.current = replaceModel(
        viewer,
        optimizedFlexModelRef.current,
        localPair?.flex_receptor_optimized ?? null,
        `${runId}:flex-optimized:${localPair?.flex_receptor_optimized?.relative_path || ""}:${refreshKey}`,
        { stick: { radius: 0.2, colorscheme: "orangeCarbon" } },
      );
      if (showInputPose) inputPoseModelRef.current?.model.show();
      else inputPoseModelRef.current?.model.hide();
      if (showOptimizedPose) optimizedPoseModelRef.current?.model.show();
      else optimizedPoseModelRef.current?.model.hide();
      if (showInputPose) inputFlexModelRef.current?.model.show();
      else inputFlexModelRef.current?.model.hide();
      if (showOptimizedPose) optimizedFlexModelRef.current?.model.show();
      else optimizedFlexModelRef.current?.model.hide();
    } else {
      inputPoseModelRef.current = replaceModel(viewer, inputPoseModelRef.current, null, "", {});
      optimizedPoseModelRef.current = replaceModel(viewer, optimizedPoseModelRef.current, null, "", {});
      inputFlexModelRef.current = replaceModel(viewer, inputFlexModelRef.current, null, "", {});
      optimizedFlexModelRef.current = replaceModel(viewer, optimizedFlexModelRef.current, null, "", {});
      const matchingPose = pose?.ok && loadedPoseModeRef.current === mode ? pose : null;
      poseModelRef.current = replaceModel(
        viewer,
        poseModelRef.current,
        matchingPose,
        `${sourceIdentity}:${mode}:${poseKind || "default"}:${pose?.relative_path || ""}:${refreshKey}`,
        {
          stick: { radius: 0.25, colorscheme: "greenCarbon" },
          sphere: { scale: 0.22 },
        },
      );
      if (showPose) poseModelRef.current?.model.show();
      else poseModelRef.current?.model.hide();
    }

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
    isLocalPosePair,
    localPair,
    mode,
    pose,
    poseKind,
    refreshKey,
    replaceModel,
    runId,
    sourceIdentity,
    sceneReceptor,
    showAxes,
    showInputPose,
    showOptimizedPose,
    showPose,
    showReceptor,
  ]);

  useEffect(() => {
    const identity = `${projectDir}|${sourceIdentity}|${isLocalPosePair ? "local-pair" : poseKind || "default"}|${poseLabel || ""}`;
    if (identityRef.current === identity) return;
    identityRef.current = identity;
    sceneGenerationRef.current += 1;
    receptorLoadGenerationRef.current += 1;
    poseLoadGenerationRef.current += 1;
    pairLoadGenerationRef.current += 1;
    screeningLoadGenerationRef.current += 1;
    hasFitRef.current = false;
    loadedPoseModeRef.current = null;
    setReceptor(null);
    setPose(null);
    setLocalPair(null);
    setPairLoadDone(false);
    setMessage(isLocalPosePair ? "正在加载局部优化前后姿势…" : `正在加载 ${displayPoseLabel}…`);
    setMessageTone("status");
    setIsSpinning(false);
    const viewer = viewerRef.current;
    (viewer as unknown as { spin?: (axis: string | boolean, speed?: number) => void } | null)?.spin?.(false);
    for (const record of [
      receptorModelRef.current,
      poseModelRef.current,
      inputPoseModelRef.current,
      optimizedPoseModelRef.current,
      inputFlexModelRef.current,
      optimizedFlexModelRef.current,
    ]) {
      if (viewer && record) viewer.removeModel(record.model);
    }
    receptorModelRef.current = null;
    poseModelRef.current = null;
    inputPoseModelRef.current = null;
    optimizedPoseModelRef.current = null;
    inputFlexModelRef.current = null;
    optimizedFlexModelRef.current = null;
    viewer?.removeAllShapes();
    viewer?.removeAllLabels();
    viewer?.render();
  }, [displayPoseLabel, isLocalPosePair, poseKind, poseLabel, projectDir, sourceIdentity]);

  useEffect(() => {
    let observer: ResizeObserver | null = null;
    let cancelled = false;
    void ensureViewer().then((viewer) => {
      if (cancelled || !viewer || !containerRef.current) return;
      observer = new ResizeObserver(() => {
        viewer.resize();
        viewer.render();
      });
      observer.observe(containerRef.current);
    });
    return () => {
      cancelled = true;
      observer?.disconnect();
    };
  }, [ensureViewer]);

  useEffect(() => {
    if (isLocalPosePair || isScreeningPose) return;
    let cancelled = false;
    const generation = ++receptorLoadGenerationRef.current;
    async function loadReceptor() {
      try {
        const payload = await invoke<string>("load_structure_for_viewer", {
          projectDir,
          fileKind: "receptor_prepared",
        });
        if (!cancelled && generation === receptorLoadGenerationRef.current) {
          setReceptor(parseStructure(payload));
        }
      } catch (error) {
        if (!cancelled && generation === receptorLoadGenerationRef.current) {
          setMessage(error instanceof Error ? error.message : "受体加载失败");
          setMessageTone("error");
        }
      }
    }
    void loadReceptor();
    return () => {
      cancelled = true;
    };
  }, [isLocalPosePair, isScreeningPose, projectDir, refreshKey]);

  useEffect(() => {
    if (isLocalPosePair || isScreeningPose) return;
    let cancelled = false;
    const generation = ++poseLoadGenerationRef.current;
    loadedPoseModeRef.current = null;
    setPose(null);
    async function loadPose() {
      setMessage(`正在读取 ${displayPoseLabel}…`);
      setMessageTone("status");
      try {
        const payload = await invoke<string>("load_docking_pose_for_viewer", {
          projectDir,
          runId,
          mode,
          poseKind,
        });
        if (cancelled || generation !== poseLoadGenerationRef.current) return;
        const poseData = parseStructure(payload);
        const matchesRequestedMode = poseData.ok && (poseData.mode === undefined || poseData.mode === mode);
        const matchesRequestedKind = !poseKind || poseData.pose_kind === undefined || poseData.pose_kind === poseKind;
        if (!matchesRequestedMode || !matchesRequestedKind) {
          loadedPoseModeRef.current = null;
          setPose(null);
          setMessage(responseErrorMessage(poseData, "姿势文件与所选结果不匹配，请重新加载"));
          setMessageTone("error");
          return;
        }
        loadedPoseModeRef.current = mode;
        setPose(poseData);
        setMessage(`${displayPoseLabel}已加载`);
        setMessageTone("status");
      } catch (error) {
        if (!cancelled && generation === poseLoadGenerationRef.current) {
          loadedPoseModeRef.current = null;
          setPose(null);
          setMessage(error instanceof Error ? error.message : "构象加载失败");
          setMessageTone("error");
        }
      }
    }
    void loadPose();
    return () => {
      cancelled = true;
    };
  }, [displayPoseLabel, isLocalPosePair, isScreeningPose, mode, poseKind, projectDir, refreshKey, runId]);

  useEffect(() => {
    if (!isScreeningPose || isLocalPosePair) return;
    let cancelled = false;
    const generation = ++screeningLoadGenerationRef.current;
    loadedPoseModeRef.current = null;
    setReceptor(null);
    setPose(null);
    setMessage(`正在核对并读取 ${displayPoseLabel}…`);
    setMessageTone("status");
    async function loadScreeningPose() {
      try {
        const payload = screeningArchiveId
          ? await invoke<string>("load_archived_screening_pose_for_viewer", {
              projectDir,
              archiveId: screeningArchiveId,
              itemId: screeningItemId,
              mode,
            })
          : await invoke<string>("load_screening_pose_for_viewer", {
              projectDir,
              itemId: screeningItemId,
              mode,
            });
        if (cancelled || generation !== screeningLoadGenerationRef.current) return;
        const result = parseScreeningPose(payload);
        if (!result.ok || !result.receptor?.ok || !result.pose?.ok || result.mode !== mode) {
          setReceptor(result.receptor ?? null);
          setPose(result.pose ?? null);
          setMessage(responseErrorMessage(result, "批量筛选构象未能完整加载"));
          setMessageTone("error");
          return;
        }
        loadedPoseModeRef.current = mode;
        setReceptor(result.receptor);
        setPose(result.pose);
        setMessage(
          result.integrity?.status === "legacy_unverified"
            ? `${displayPoseLabel}已加载；未完全验证：历史记录缺少完成时输出哈希`
            : `${displayPoseLabel}已核对并加载`,
        );
        setMessageTone(result.integrity?.status === "legacy_unverified" ? "error" : "status");
      } catch (error) {
        if (!cancelled && generation === screeningLoadGenerationRef.current) {
          loadedPoseModeRef.current = null;
          setReceptor(null);
          setPose(null);
          setMessage(error instanceof Error ? error.message : "批量筛选构象加载失败");
          setMessageTone("error");
        }
      }
    }
    void loadScreeningPose();
    return () => {
      cancelled = true;
    };
  }, [
    displayPoseLabel,
    isLocalPosePair,
    isScreeningPose,
    mode,
    projectDir,
    refreshKey,
    screeningArchiveId,
    screeningItemId,
  ]);

  useEffect(() => {
    if (!isLocalPosePair) return;
    let cancelled = false;
    const generation = ++pairLoadGenerationRef.current;
    setPairLoadDone(false);
    setLocalPair(null);
    setMessage("正在核对并加载局部优化前后姿势…");
    setMessageTone("status");
    async function loadPair() {
      try {
        const payload = await invoke<string>("load_local_only_pose_pair_for_viewer", {
          projectDir,
          runId,
        });
        if (cancelled || generation !== pairLoadGenerationRef.current) return;
        const pair = parseLocalPosePair(payload);
        setPairLoadDone(true);
        if (!pair.ok || !pair.receptor?.ok || !pair.input?.ok || !pair.optimized?.ok) {
          setLocalPair(pair);
          setMessage(responseErrorMessage(pair, "局部优化前后姿势未能完整加载"));
          setMessageTone("error");
          return;
        }
        setLocalPair(pair);
        setMessage(
          pair.integrity?.status === "legacy_unverified"
            ? "历史姿势已叠合；该 run 缺少执行时完整性证据"
            : "输入姿势与优化后姿势已按原始坐标叠合",
        );
        setMessageTone(pair.integrity?.status === "legacy_unverified" ? "error" : "status");
      } catch (error) {
        if (!cancelled && generation === pairLoadGenerationRef.current) {
          setPairLoadDone(true);
          setLocalPair(null);
          setMessage(error instanceof Error ? error.message : "局部优化前后姿势加载失败");
          setMessageTone("error");
        }
      }
    }
    void loadPair();
    return () => {
      cancelled = true;
    };
  }, [isLocalPosePair, projectDir, refreshKey, runId]);

  useEffect(() => {
    const sceneReady = isLocalPosePair
      ? pairLoadDone && Boolean(
          localPair?.receptor?.ok
          || localPair?.input?.ok
          || localPair?.optimized?.ok
          || localPair?.flex_receptor_input?.ok
          || localPair?.flex_receptor_optimized?.ok
        )
      : Boolean(receptor?.ok || pose?.ok);
    const shouldFit = !hasFitRef.current && sceneReady;
    void renderScene(shouldFit)
      .then(() => {
        if (shouldFit) hasFitRef.current = true;
      })
      .catch((error) => {
        setMessage(error instanceof Error ? error.message : "3D 场景渲染失败");
        setMessageTone("error");
      });
  }, [isLocalPosePair, localPair, pairLoadDone, pose, receptor, renderScene]);

  useEffect(() => {
    if (!focusRequest || focusRequest.mode !== mode) return;
    const focusModels = isLocalPosePair
      ? [
          ...(showInputPose && inputPoseModelRef.current ? [inputPoseModelRef.current.model] : []),
          ...(showInputPose && inputFlexModelRef.current ? [inputFlexModelRef.current.model] : []),
          ...(showOptimizedPose && optimizedPoseModelRef.current ? [optimizedPoseModelRef.current.model] : []),
          ...(showOptimizedPose && optimizedFlexModelRef.current ? [optimizedFlexModelRef.current.model] : []),
        ]
      : poseModelRef.current
        ? [poseModelRef.current.model]
        : [];
    if (!focusModels.length) return;
    let cancelled = false;
    void renderScene(false).then(() => {
      const viewer = viewerRef.current;
      if (cancelled || !viewer) return;
      const modelSelection = focusModels.length === 1 ? focusModels[0] : focusModels;
      viewer.zoomTo({ model: modelSelection } as never, 320);
      viewer.render();
      setMessage(
        isLocalPosePair && localPoseView
          ? `已定位到${localViewLabel(localPoseView)}`
          : `已定位到 ${displayPoseLabel}`,
      );
      setMessageTone("status");
    });
    return () => {
      cancelled = true;
    };
  }, [
    displayPoseLabel,
    focusRequest,
    isLocalPosePair,
    localPoseView,
    mode,
    renderScene,
    showInputPose,
    showOptimizedPose,
  ]);

  useEffect(() => () => {
    const viewer = viewerRef.current as unknown as { spin?: (axis: string | boolean, speed?: number) => void } | null;
    viewer?.spin?.(false);
    viewerRef.current?.clear();
    viewerRef.current = null;
    viewerInitRef.current = null;
    receptorModelRef.current = null;
    poseModelRef.current = null;
    inputPoseModelRef.current = null;
    optimizedPoseModelRef.current = null;
    inputFlexModelRef.current = null;
    optimizedFlexModelRef.current = null;
    loadedPoseModeRef.current = null;
    if (containerRef.current) containerRef.current.replaceChildren();
  }, []);

  const zoom = (factor: number) => {
    viewerRef.current?.zoom(factor);
    viewerRef.current?.render();
  };

  const toggleSpin = () => {
    const viewer = viewerRef.current as unknown as { spin?: (axis: string | boolean, speed?: number) => void } | null;
    const next = !isSpinning;
    if (next) viewer?.spin?.("y", 0.7);
    else viewer?.spin?.(false);
    setIsSpinning(next);
  };

  const toggleLocalPose = (kind: LocalPoseKind) => {
    if (!localPoseView || !onLocalPoseViewChange) return;
    if (kind === "input") {
      if (localPoseView === "input") {
        setMessage("叠合视图至少保留一个姿势");
        setMessageTone("status");
        return;
      }
      onLocalPoseViewChange(localPoseView === "overlay" ? "optimized" : "overlay");
      return;
    }
    if (localPoseView === "optimized") {
      setMessage("叠合视图至少保留一个姿势");
      setMessageTone("status");
      return;
    }
    onLocalPoseViewChange(localPoseView === "overlay" ? "input" : "overlay");
  };

  const canvasLabel = isLocalPosePair && localPoseView
    ? `局部优化结果 3D 视图：${localViewLabel(localPoseView)}。输入姿势为青色细棒，优化后姿势为橙色粗棒和小球。`
    : `${displayPoseLabel} 3D 视图。`;

  return (
    <div
      className={`run-preview pose-structure-preview ${className}`.trim()}
      aria-label="构象 3D 预览"
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
        aria-label={canvasLabel}
        className="run-preview-canvas"
        style={{ height: "100%" }}
        ref={containerRef}
        role="img"
        tabIndex={0}
      />
      <div className="run-preview-legend">
        <button
          type="button"
          className={`legend-toggle-btn ${showReceptor ? "is-active" : "is-inactive"}`}
          onClick={() => setShowReceptor(!showReceptor)}
          title={showReceptor ? "隐藏受体" : "显示受体"}
          aria-pressed={showReceptor}
        >
          <i className={`run-preview-dot receptor ${showReceptor ? "" : "muted"}`} />
          受体
        </button>
        {isLocalPosePair ? (
          <>
            <button
              type="button"
              className={`legend-toggle-btn ${showInputPose ? "is-active" : "is-inactive"}`}
              onClick={() => toggleLocalPose("input")}
              title={showInputPose ? "隐藏输入姿势" : "显示输入姿势"}
              aria-pressed={showInputPose}
            >
              <i className={`run-preview-dot input-pose ${showInputPose ? "" : "muted"}`} />
              输入姿势
            </button>
            <button
              type="button"
              className={`legend-toggle-btn ${showOptimizedPose ? "is-active" : "is-inactive"}`}
              onClick={() => toggleLocalPose("optimized")}
              title={showOptimizedPose ? "隐藏优化后姿势" : "显示优化后姿势"}
              aria-pressed={showOptimizedPose}
            >
              <i className={`run-preview-dot optimized-pose ${showOptimizedPose ? "" : "muted"}`} />
              优化后姿势
            </button>
          </>
        ) : (
          <button
            type="button"
            className={`legend-toggle-btn ${showPose ? "is-active" : "is-inactive"}`}
            onClick={() => setShowPose(!showPose)}
            title={showPose ? "隐藏构象" : "显示构象"}
            aria-pressed={showPose}
          >
            <i className={`run-preview-dot ligand ${showPose ? "" : "muted"}`} />
            {displayPoseLabel}
          </button>
        )}
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
