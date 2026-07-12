import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowsOut,
  Cube,
  MagnifyingGlassMinus,
  MagnifyingGlassPlus,
  Pause,
  Play,
  CornersOut,
  CornersIn,
} from "@phosphor-icons/react";
import * as $3Dmol from "3dmol";
import type { BoxVisualizationPayload, DockStartProject, ViewerStructureResult } from "../types";
import { addOrientationAxes } from "./viewerSceneHelpers";

export type RunBoxFieldKey = keyof DockStartProject["box"];

const runBoxFieldLabels: Record<RunBoxFieldKey, string> = {
  center_x: "中心 X",
  center_y: "中心 Y",
  center_z: "中心 Z",
  size_x: "尺寸 X",
  size_y: "尺寸 Y",
  size_z: "尺寸 Z",
};

type RunStructurePreviewProps = {
  projectDir: string;
  box: DockStartProject["box"];
  refreshKey?: number;
  wheelBinding?: RunBoxFieldKey | null;
  onWheelAdjust?: (direction: 1 | -1) => void;
};

type PreviewStructures = {
  receptor: ViewerStructureResult | null;
  ligand: ViewerStructureResult | null;
};

function parseStructure(rawPayload: string): ViewerStructureResult {
  return JSON.parse(rawPayload) as ViewerStructureResult;
}

function buildBoxVisualization(box: DockStartProject["box"]): BoxVisualizationPayload | null {
  const { center_x, center_y, center_z, size_x, size_y, size_z } = box;
  if (![center_x, center_y, center_z, size_x, size_y, size_z].every(Number.isFinite)) return null;
  if (size_x <= 0 || size_y <= 0 || size_z <= 0) return null;
  const min = { x: center_x - size_x / 2, y: center_y - size_y / 2, z: center_z - size_z / 2 };
  const max = { x: center_x + size_x / 2, y: center_y + size_y / 2, z: center_z + size_z / 2 };
  return {
    center_x,
    center_y,
    center_z,
    size_x,
    size_y,
    size_z,
    unit: "angstrom",
    min,
    max,
    corners: [
      { x: min.x, y: min.y, z: min.z },
      { x: min.x, y: min.y, z: max.z },
      { x: min.x, y: max.y, z: min.z },
      { x: min.x, y: max.y, z: max.z },
      { x: max.x, y: min.y, z: min.z },
      { x: max.x, y: min.y, z: max.z },
      { x: max.x, y: max.y, z: min.z },
      { x: max.x, y: max.y, z: max.z },
    ],
    viewer_box_payload: {
      center: { x: center_x, y: center_y, z: center_z },
      dimensions: { w: size_x, h: size_y, d: size_z },
      color: "#f0a23b",
      alpha: 0.08,
      wireframe: true,
    },
  };
}

function addBoxOverlay(viewer: ReturnType<typeof $3Dmol.createViewer>, box: BoxVisualizationPayload): void {
  const target = viewer as unknown as {
    addBox: (spec: Record<string, unknown>) => void;
    addCylinder?: (spec: Record<string, unknown>) => void;
    addSphere?: (spec: Record<string, unknown>) => void;
  };
  target.addBox({ ...box.viewer_box_payload, color: "#f0a23b", alpha: 0.08, wireframe: false });
  const edges = [
    [0, 1], [0, 2], [0, 4], [3, 1], [3, 2], [3, 7],
    [5, 1], [5, 4], [5, 7], [6, 2], [6, 4], [6, 7],
  ];
  for (const [from, to] of edges) {
    target.addCylinder?.({
      start: box.corners[from],
      end: box.corners[to],
      radius: 0.07,
      color: "#f0a23b",
      fromCap: 1,
      toCap: 1,
    });
  }
  target.addSphere?.({ center: box.viewer_box_payload.center, radius: 0.22, color: "#79b9ec", alpha: 0.9 });
}

export default function RunStructurePreview({
  projectDir,
  box,
  refreshKey = 0,
  wheelBinding = null,
  onWheelAdjust,
}: RunStructurePreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ReturnType<typeof $3Dmol.createViewer> | null>(null);
  const boxRef = useRef(box);
  const wheelAccumulatorRef = useRef(0);
  const wheelResetRef = useRef<number | null>(null);
  const [structures, setStructures] = useState<PreviewStructures>({ receptor: null, ligand: null });
  const [message, setMessage] = useState("正在读取受体、配体与搜索范围…");
  const [isSpinning, setIsSpinning] = useState(false);

  // 轻量图层控制状态
  const [showReceptor, setShowReceptor] = useState(true);
  const [showLigand, setShowLigand] = useState(true);
  const [showBox, setShowBox] = useState(true);
  const [isFullscreen, setIsFullscreen] = useState(false);

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

    // 条件渲染受体
    if (showReceptor && structures.receptor?.ok) {
      const model = viewer.addModel(structures.receptor.content, structures.receptor.format);
      model.setStyle({}, {
        cartoon: { color: "spectrum", opacity: 0.74 },
        stick: { radius: 0.1, colorscheme: "Jmol" },
      });
    }

    // 条件渲染配体
    if (showLigand && structures.ligand?.ok) {
      const model = viewer.addModel(structures.ligand.content, structures.ligand.format);
      model.setStyle({}, { stick: { radius: 0.28, colorscheme: "greenCarbon" }, sphere: { scale: 0.24 } });
    }

    // 条件渲染 Box
    const boxVisualization = buildBoxVisualization(boxRef.current);
    if (showBox && boxVisualization) {
      addBoxOverlay(viewer, boxVisualization);
    }

    addOrientationAxes(viewer, boxVisualization);
    if (previousView) {
      (viewer as unknown as { setView?: (view: unknown) => void }).setView?.(previousView);
    } else {
      viewer.zoomTo();
    }
    viewer.render();
  }, [ensureViewer, structures, showReceptor, showLigand, showBox]);

  const refreshBoxOverlay = useCallback(() => {
    const viewer = ensureViewer();
    if (!viewer) return;
    (viewer as unknown as { removeAllShapes?: () => void }).removeAllShapes?.();
    (viewer as unknown as { removeAllLabels?: () => void }).removeAllLabels?.();
    const boxVisualization = buildBoxVisualization(boxRef.current);
    if (showBox && boxVisualization) addBoxOverlay(viewer, boxVisualization);
    addOrientationAxes(viewer, boxVisualization);
    viewer.render();
  }, [ensureViewer, showBox]);

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
    const container = containerRef.current;
    if (!container || !wheelBinding || !onWheelAdjust) {
      wheelAccumulatorRef.current = 0;
      return;
    }

    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const normalizedDelta = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? event.deltaY * 16 : event.deltaY;
      wheelAccumulatorRef.current += normalizedDelta;
      if (Math.abs(wheelAccumulatorRef.current) >= 36) {
        onWheelAdjust(wheelAccumulatorRef.current < 0 ? 1 : -1);
        wheelAccumulatorRef.current = 0;
      }
      if (wheelResetRef.current !== null) window.clearTimeout(wheelResetRef.current);
      wheelResetRef.current = window.setTimeout(() => {
        wheelAccumulatorRef.current = 0;
        wheelResetRef.current = null;
      }, 160);
    };

    container.addEventListener("wheel", handleWheel, { passive: false, capture: true });
    return () => {
      container.removeEventListener("wheel", handleWheel, { capture: true });
      if (wheelResetRef.current !== null) window.clearTimeout(wheelResetRef.current);
      wheelResetRef.current = null;
      wheelAccumulatorRef.current = 0;
    };
  }, [onWheelAdjust, wheelBinding]);

  useEffect(() => {
    let cancelled = false;
    async function loadPreview() {
      setMessage("正在读取受体、配体与搜索范围…");
      try {
        const [receptorPayload, ligandPayload] = await Promise.all([
          invoke<string>("load_structure_for_viewer", { projectDir, fileKind: "receptor_prepared" }),
          invoke<string>("load_structure_for_viewer", { projectDir, fileKind: "ligand_prepared" }),
        ]);
        if (cancelled) return;
        const receptor = parseStructure(receptorPayload);
        const ligand = parseStructure(ligandPayload);
        setStructures({ receptor, ligand });
        if (receptor.ok && ligand.ok) setMessage("受体、配体与搜索范围已加载");
        else if (receptor.ok || ligand.ok) setMessage("部分结构可显示；请查看运行前检查");
        else setMessage("尚无可显示的 prepared PDBQT");
      } catch (error) {
        if (!cancelled) setMessage(error instanceof Error ? error.message : "结构预览加载失败");
      }
    }
    void loadPreview();
    return () => {
      cancelled = true;
    };
  }, [projectDir, refreshKey]);

  useEffect(() => {
    try {
      renderScene(true);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "3D 场景渲染失败");
    }
  }, [renderScene, structures, showReceptor, showLigand, showBox]);

  useEffect(() => {
    boxRef.current = box;
    try {
      refreshBoxOverlay();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "搜索范围预览刷新失败");
    }
  }, [box, refreshBoxOverlay]);

  // 全屏变化时的自适应调整
  useEffect(() => {
    const timer = setTimeout(() => {
      if (viewerRef.current) {
        viewerRef.current.resize();
        viewerRef.current.render();
      }
    }, 120);
    return () => clearTimeout(timer);
  }, [isFullscreen]);

  useEffect(() => {
    if (!isFullscreen) return;
    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsFullscreen(false);
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isFullscreen]);

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

  const toggleFullscreen = () => {
    setIsFullscreen((prev) => !prev);
  };

  return (
    <section className={`run-preview ${isFullscreen ? "is-fullscreen" : ""}`} aria-label="运行前结构预览">
      <div
        className="run-preview-canvas"
        data-wheel-bound={wheelBinding || undefined}
        ref={containerRef}
        tabIndex={0}
        aria-label={
          wheelBinding
            ? `受体、配体和搜索范围三维视图；滚轮当前调整${runBoxFieldLabels[wheelBinding]}`
            : "受体、配体和搜索范围三维视图；滚轮缩放"
        }
      />

      {isFullscreen ? (
        <div className="run-preview-fullscreen-controls">
          <div className="fullscreen-toggles">
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
              className={`legend-toggle-btn ${showLigand ? "is-active" : "is-inactive"}`}
              onClick={() => setShowLigand(!showLigand)}
              title={showLigand ? "隐藏配体" : "显示配体"}
              aria-pressed={showLigand}
            >
              <i className={`run-preview-dot ligand ${showLigand ? "" : "muted"}`} />
              配体
            </button>
            <button
              type="button"
              className={`legend-toggle-btn ${showBox ? "is-active" : "is-inactive"}`}
              onClick={() => setShowBox(!showBox)}
              title={showBox ? "隐藏搜索范围" : "显示搜索范围"}
              aria-pressed={showBox}
            >
              <Cube className={showBox ? "" : "muted"} aria-hidden="true" size={14} />
              搜索范围
            </button>
          </div>
          <div className="fullscreen-actions">
            <button type="button" onClick={() => renderScene(true)} className="secondary-button compact-btn">
              适应视图
            </button>
            <button type="button" onClick={toggleFullscreen} className="primary-button compact-btn fullscreen-close-btn">
              <CornersIn size={14} /> 关闭全屏
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="run-preview-toolbar" aria-label="3D 视图工具">
            <button type="button" onClick={() => zoom(1.18)} title="放大" aria-label="放大结构">
              <MagnifyingGlassPlus size={18} />
            </button>
            <button type="button" onClick={() => zoom(0.84)} title="缩小" aria-label="缩小结构">
              <MagnifyingGlassMinus size={18} />
            </button>
            <button type="button" onClick={() => renderScene(true)} title="适应窗口" aria-label="让结构适应窗口">
              <ArrowsOut size={18} />
            </button>
            <button type="button" onClick={toggleSpin} title={isSpinning ? "停止旋转" : "自动旋转"} aria-label={isSpinning ? "停止自动旋转" : "开始自动旋转"}>
              {isSpinning ? <Pause size={18} /> : <Play size={18} />}
            </button>
            <button type="button" onClick={toggleFullscreen} title="全屏查看" aria-label="全屏查看">
              <CornersOut size={18} />
            </button>
          </div>
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
              className={`legend-toggle-btn ${showLigand ? "is-active" : "is-inactive"}`}
              onClick={() => setShowLigand(!showLigand)}
              title={showLigand ? "隐藏配体" : "显示配体"}
              aria-pressed={showLigand}
            >
              <i className={`run-preview-dot ligand ${showLigand ? "" : "muted"}`} />
              配体
            </button>
            <button
              type="button"
              className={`legend-toggle-btn ${showBox ? "is-active" : "is-inactive"}`}
              onClick={() => setShowBox(!showBox)}
              title={showBox ? "隐藏搜索范围" : "显示搜索范围"}
              aria-pressed={showBox}
            >
              <Cube className={showBox ? "" : "muted"} aria-hidden="true" size={14} />
              搜索范围
            </button>
            <strong>{message}</strong>
          </div>
        </>
      )}
    </section>
  );
}
