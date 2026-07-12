import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowClockwise,
  ArrowsOut,
  MagnifyingGlassMinus,
  MagnifyingGlassPlus,
  Pause,
  Play,
} from "@phosphor-icons/react";
import * as $3Dmol from "3dmol";
import type { ViewerStructureResult } from "../types";
import { addOrientationAxes } from "./viewerSceneHelpers";

type PoseStructurePreviewProps = {
  projectDir: string;
  runId: string;
  mode: number;
  refreshKey?: number;
};

function parseStructure(rawPayload: string): ViewerStructureResult {
  return JSON.parse(rawPayload) as ViewerStructureResult;
}

export default function PoseStructurePreview({
  projectDir,
  runId,
  mode,
  refreshKey = 0,
}: PoseStructurePreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ReturnType<typeof $3Dmol.createViewer> | null>(null);
  const [receptor, setReceptor] = useState<ViewerStructureResult | null>(null);
  const [pose, setPose] = useState<ViewerStructureResult | null>(null);
  const [message, setMessage] = useState("正在加载结构与构象…");
  const [isSpinning, setIsSpinning] = useState(false);
  const [showReceptor, setShowReceptor] = useState(true);
  const [showPose, setShowPose] = useState(true);

  const ensureViewer = useCallback(() => {
    if (viewerRef.current) return viewerRef.current;
    if (!containerRef.current) return null;
    const background = getComputedStyle(document.documentElement).getPropertyValue("--ds-viewer-bg").trim();
    viewerRef.current = $3Dmol.createViewer(containerRef.current, { backgroundColor: background || "#061c31" });
    return viewerRef.current;
  }, []);

  const renderScene = useCallback((fit = false) => {
    const viewer = ensureViewer();
    if (!viewer) return;
    const previousView = fit ? null : (viewer as unknown as { getView?: () => unknown }).getView?.();
    viewer.clear();

    if (showReceptor && receptor?.ok) {
      const model = viewer.addModel(receptor.content, receptor.format);
      model.setStyle({}, {
        cartoon: { color: "spectrum", opacity: 0.74 },
        stick: { radius: 0.1, colorscheme: "Jmol" },
      });
    }

    if (showPose && pose?.ok) {
      const model = viewer.addModel(pose.content, pose.format);
      model.setStyle({}, { stick: { radius: 0.28, colorscheme: "greenCarbon" }, sphere: { scale: 0.24 } });
    }

    addOrientationAxes(viewer, null);

    if (previousView) {
      (viewer as unknown as { setView?: (view: unknown) => void }).setView?.(previousView);
    } else {
      viewer.zoomTo();
    }
    viewer.render();
  }, [ensureViewer, receptor, pose, showReceptor, showPose]);

  useEffect(() => {
    const viewer = ensureViewer();
    if (!viewer || !containerRef.current) return;
    const observer = new ResizeObserver(() => {
      viewer.resize();
      viewer.render();
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [ensureViewer]);

  useEffect(() => {
    let cancelled = false;
    async function loadPoseData() {
      setMessage(`正在读取 Mode ${mode} 的对接构象…`);
      try {
        const [receptorPayload, posePayload] = await Promise.all([
          invoke<string>("load_structure_for_viewer", { projectDir, fileKind: "receptor_prepared" }),
          invoke<string>("load_docking_pose_for_viewer", { projectDir, runId, mode }),
        ]);
        if (cancelled) return;
        const recData = parseStructure(receptorPayload);
        const poseData = parseStructure(posePayload);
        setReceptor(recData);
        setPose(poseData);
        if (recData.ok && poseData.ok) {
          setMessage(`构象 Mode ${mode} 已加载`);
        } else {
          setMessage("结构或构象文件加载有误，请确认输入");
        }
      } catch (error) {
        if (!cancelled) setMessage(error instanceof Error ? error.message : "构象加载失败");
      }
    }
    void loadPoseData();
    return () => {
      cancelled = true;
    };
  }, [projectDir, runId, mode, refreshKey]);

  useEffect(() => {
    try {
      renderScene(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "3D 场景渲染失败");
    }
  }, [renderScene, receptor, pose, showReceptor, showPose]);

  useEffect(() => () => {
    viewerRef.current?.clear();
    viewerRef.current = null;
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
    <div className="run-preview" style={{ height: "420px", minHeight: "420px" }} aria-label="构象 3D 预览">
      <div className="run-preview-toolbar" aria-label="3D 视图工具">
        <button type="button" onClick={() => zoom(1.18)} title="放大" aria-label="放大">
          <MagnifyingGlassPlus size={18} />
        </button>
        <button type="button" onClick={() => zoom(0.84)} title="缩小" aria-label="缩小">
          <MagnifyingGlassMinus size={18} />
        </button>
        <button type="button" onClick={() => renderScene(true)} title="适应窗口" aria-label="适应窗口">
          <ArrowsOut size={18} />
        </button>
        <button type="button" onClick={() => renderScene(true)} title="重置视角" aria-label="重置视角">
          <ArrowClockwise size={18} />
        </button>
        <button type="button" onClick={toggleSpin} title={isSpinning ? "停止旋转" : "自动旋转"} aria-label="旋转">
          {isSpinning ? <Pause size={18} /> : <Play size={18} />}
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
