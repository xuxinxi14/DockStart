import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowsOut,
  MagnifyingGlassMinus,
  MagnifyingGlassPlus,
  Pause,
  Play,
  Crosshair,
} from "@phosphor-icons/react";
import type { ViewerStructureResult } from "../types";
import { addOrientationAxes } from "./viewerSceneHelpers";
import {
  load3Dmol,
  structureFingerprint,
  type ThreeDmolModel,
  type ThreeDmolViewer,
} from "./threeDmolLoader";

type PoseStructurePreviewProps = {
  projectDir: string;
  runId: string;
  mode: number;
  refreshKey?: number;
  className?: string;
};

function parseStructure(rawPayload: string): ViewerStructureResult {
  return JSON.parse(rawPayload) as ViewerStructureResult;
}

export default function PoseStructurePreview({
  projectDir,
  runId,
  mode,
  refreshKey = 0,
  className = "",
}: PoseStructurePreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ThreeDmolViewer | null>(null);
  const viewerInitRef = useRef<Promise<ThreeDmolViewer | null> | null>(null);
  const receptorModelRef = useRef<{ fingerprint: string; model: ThreeDmolModel } | null>(null);
  const poseModelRef = useRef<{ fingerprint: string; model: ThreeDmolModel } | null>(null);
  const hasFitRef = useRef(false);
  const identityRef = useRef("");
  const sceneGenerationRef = useRef(0);
  const receptorLoadGenerationRef = useRef(0);
  const poseLoadGenerationRef = useRef(0);
  const loadedPoseModeRef = useRef<number | null>(null);
  const [receptor, setReceptor] = useState<ViewerStructureResult | null>(null);
  const [pose, setPose] = useState<ViewerStructureResult | null>(null);
  const [message, setMessage] = useState("正在加载结构与构象…");
  const [isSpinning, setIsSpinning] = useState(false);
  const [showReceptor, setShowReceptor] = useState(true);
  const [showPose, setShowPose] = useState(true);
  const [showAxes, setShowAxes] = useState(true);

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

  const renderScene = useCallback(async (fit = false) => {
    const sceneGeneration = sceneGenerationRef.current;
    const viewer = await ensureViewer();
    if (!viewer || sceneGeneration !== sceneGenerationRef.current) return;
    const previousView = fit ? null : (viewer as unknown as { getView?: () => unknown }).getView?.();

    const receptorFingerprint = receptor?.ok
      ? structureFingerprint(receptor.content, receptor.format, `${receptor.relative_path}:${refreshKey}`)
      : "";
    if (receptorFingerprint && receptorModelRef.current?.fingerprint !== receptorFingerprint) {
      if (receptorModelRef.current) viewer.removeModel(receptorModelRef.current.model);
      const model = viewer.addModel(receptor!.content, receptor!.format);
      model.setStyle({}, {
        cartoon: { color: "spectrum", opacity: 0.74 },
        stick: { radius: 0.1, colorscheme: "Jmol" },
      });
      receptorModelRef.current = { fingerprint: receptorFingerprint, model };
    } else if (!receptorFingerprint && receptorModelRef.current) {
      viewer.removeModel(receptorModelRef.current.model);
      receptorModelRef.current = null;
    }
    if (showReceptor) receptorModelRef.current?.model.show();
    else receptorModelRef.current?.model.hide();

    const poseFingerprint = pose?.ok && loadedPoseModeRef.current === mode
      ? structureFingerprint(pose.content, pose.format, `${runId}:${mode}:${pose.relative_path}:${refreshKey}`)
      : "";
    if (poseFingerprint && poseModelRef.current?.fingerprint !== poseFingerprint) {
      if (poseModelRef.current) viewer.removeModel(poseModelRef.current.model);
      const model = viewer.addModel(pose!.content, pose!.format);
      model.setStyle({}, { stick: { radius: 0.28, colorscheme: "greenCarbon" }, sphere: { scale: 0.24 } });
      poseModelRef.current = { fingerprint: poseFingerprint, model };
    } else if (!poseFingerprint && poseModelRef.current) {
      viewer.removeModel(poseModelRef.current.model);
      poseModelRef.current = null;
    }
    if (showPose) poseModelRef.current?.model.show();
    else poseModelRef.current?.model.hide();

    viewer.removeAllShapes();
    viewer.removeAllLabels();
    if (showAxes) addOrientationAxes(viewer, null);

    if (previousView) {
      (viewer as unknown as { setView?: (view: unknown) => void }).setView?.(previousView);
    } else {
      viewer.zoomTo();
    }
    viewer.render();
  }, [ensureViewer, mode, pose, receptor, refreshKey, runId, showAxes, showPose, showReceptor]);

  useEffect(() => {
    const identity = `${projectDir}|${runId}`;
    if (identityRef.current === identity) return;
    identityRef.current = identity;
    sceneGenerationRef.current += 1;
    receptorLoadGenerationRef.current += 1;
    poseLoadGenerationRef.current += 1;
    hasFitRef.current = false;
    loadedPoseModeRef.current = null;
    setReceptor(null);
    setPose(null);
    setMessage("正在加载结构与构象…");
    setIsSpinning(false);
    const viewer = viewerRef.current;
    (viewer as unknown as { spin?: (axis: string | boolean, speed?: number) => void } | null)?.spin?.(false);
    if (viewer && receptorModelRef.current) viewer.removeModel(receptorModelRef.current.model);
    if (viewer && poseModelRef.current) viewer.removeModel(poseModelRef.current.model);
    receptorModelRef.current = null;
    poseModelRef.current = null;
    viewer?.removeAllShapes();
    viewer?.removeAllLabels();
    viewer?.render();
  }, [projectDir, runId]);

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
    let cancelled = false;
    const generation = ++receptorLoadGenerationRef.current;
    async function loadReceptor() {
      try {
        const payload = await invoke<string>("load_structure_for_viewer", { projectDir, fileKind: "receptor_prepared" });
        if (!cancelled && generation === receptorLoadGenerationRef.current) setReceptor(parseStructure(payload));
      } catch (error) {
        if (!cancelled && generation === receptorLoadGenerationRef.current) {
          setMessage(error instanceof Error ? error.message : "受体加载失败");
        }
      }
    }
    void loadReceptor();
    return () => {
      cancelled = true;
    };
  }, [projectDir, refreshKey]);

  useEffect(() => {
    let cancelled = false;
    const generation = ++poseLoadGenerationRef.current;
    loadedPoseModeRef.current = null;
    setPose(null);
    async function loadPose() {
      setMessage(`正在读取 Mode ${mode} 的对接构象…`);
      try {
        const payload = await invoke<string>("load_docking_pose_for_viewer", { projectDir, runId, mode });
        if (cancelled || generation !== poseLoadGenerationRef.current) return;
        const poseData = parseStructure(payload);
        const matchesRequestedMode = poseData.ok && (poseData.mode === undefined || poseData.mode === mode);
        if (matchesRequestedMode) {
          loadedPoseModeRef.current = mode;
          setPose(poseData);
        } else {
          loadedPoseModeRef.current = null;
          setPose(null);
        }
        setMessage(matchesRequestedMode ? `构象 Mode ${mode} 已加载` : "构象文件与所选 Mode 不匹配，请重新加载");
      } catch (error) {
        if (!cancelled && generation === poseLoadGenerationRef.current) {
          loadedPoseModeRef.current = null;
          setPose(null);
          setMessage(error instanceof Error ? error.message : "构象加载失败");
        }
      }
    }
    void loadPose();
    return () => {
      cancelled = true;
    };
  }, [mode, projectDir, refreshKey, runId]);

  useEffect(() => {
    const shouldFit = !hasFitRef.current && Boolean(receptor?.ok || pose?.ok);
    void renderScene(shouldFit)
      .then(() => {
        if (shouldFit) hasFitRef.current = true;
      })
      .catch((error) => setMessage(error instanceof Error ? error.message : "3D 场景渲染失败"));
  }, [pose, receptor, renderScene]);

  useEffect(() => () => {
    const viewer = viewerRef.current as unknown as { spin?: (axis: string | boolean, speed?: number) => void } | null;
    viewer?.spin?.(false);
    viewerRef.current?.clear();
    viewerRef.current = null;
    viewerInitRef.current = null;
    receptorModelRef.current = null;
    poseModelRef.current = null;
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

  return (
    <div className={`run-preview pose-structure-preview ${className}`.trim()} aria-label="构象 3D 预览">
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
        className="run-preview-canvas"
        style={{ height: "100%" }}
        ref={containerRef}
        tabIndex={0}
      />
      <div className="run-preview-legend" aria-live="polite">
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
        <button
          type="button"
          className={`legend-toggle-btn ${showPose ? "is-active" : "is-inactive"}`}
          onClick={() => setShowPose(!showPose)}
          title={showPose ? "隐藏构象" : "显示构象"}
          aria-pressed={showPose}
        >
          <i className={`run-preview-dot ligand ${showPose ? "" : "muted"}`} />
          配体构象 (Mode {mode})
        </button>
        <strong>{message}</strong>
      </div>
    </div>
  );
}
