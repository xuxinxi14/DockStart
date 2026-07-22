import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
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
  Crosshair,
  CheckCircle,
} from "@phosphor-icons/react";
import type { BoxVisualizationPayload, DockStartProject, ViewerStructureResult } from "../types";
import { addOrientationAxes } from "./viewerSceneHelpers";
import {
  load3Dmol,
  structureFingerprint,
  type ThreeDmolModel,
  type ThreeDmolViewer,
} from "./threeDmolLoader";
import {
  runBoxFieldLabels,
  type RunAxisSpacing,
  type RunBoxFieldKey,
  type RunBoxLineThickness,
} from "./RunBoxInspector";

type RunStructurePreviewProps = {
  projectDir: string;
  box: DockStartProject["box"];
  refreshKey?: number;
  wheelBinding?: RunBoxFieldKey | null;
  onWheelAdjust?: (direction: 1 | -1) => void;
  fullscreenInspector?: ReactNode;
  boxLineThickness?: RunBoxLineThickness;
  axisSpacing?: RunAxisSpacing;
  fitRequestKey?: number;
  residueSelectionActive?: boolean;
  selectedResidues?: string[];
  onResidueSelect?: (selector: string) => void;
  onResidueSelectionComplete?: () => void;
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

const boxLineRadii: Record<RunBoxLineThickness, number> = {
  thin: 0.08,
  standard: 0.17,
  bold: 0.32,
};

const axisSpacingScales: Record<RunAxisSpacing, number> = {
  compact: 0.85,
  standard: 1.35,
  wide: 2.1,
};

function addBoxOverlay(
  viewer: ThreeDmolViewer,
  box: BoxVisualizationPayload,
  lineThickness: RunBoxLineThickness,
): void {
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
      radius: boxLineRadii[lineThickness],
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
  fullscreenInspector,
  boxLineThickness = "standard",
  axisSpacing = "standard",
  fitRequestKey = 0,
  residueSelectionActive = false,
  selectedResidues = [],
  onResidueSelect,
  onResidueSelectionComplete,
}: RunStructurePreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ThreeDmolViewer | null>(null);
  const viewerInitRef = useRef<Promise<ThreeDmolViewer | null> | null>(null);
  const receptorModelRef = useRef<{ fingerprint: string; model: ThreeDmolModel } | null>(null);
  const ligandModelRef = useRef<{ fingerprint: string; model: ThreeDmolModel } | null>(null);
  const hasFitRef = useRef(false);
  const identityRef = useRef("");
  const sceneGenerationRef = useRef(0);
  const loadGenerationRef = useRef(0);
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
  const [showAxes, setShowAxes] = useState(true);
  const [manualFullscreen, setManualFullscreen] = useState(false);
  const isFullscreen = manualFullscreen || residueSelectionActive;
  const showFullscreenInspector = Boolean(fullscreenInspector) && !residueSelectionActive;

  const finishResidueSelection = useCallback(() => {
    setManualFullscreen(false);
    onResidueSelectionComplete?.();
  }, [onResidueSelectionComplete]);

  const closeFullscreen = useCallback(() => {
    if (residueSelectionActive) {
      finishResidueSelection();
      return;
    }
    setManualFullscreen(false);
  }, [finishResidueSelection, residueSelectionActive]);

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

    const receptorFingerprint = structures.receptor?.ok
      ? structureFingerprint(structures.receptor.content, structures.receptor.format, `${structures.receptor.relative_path}:${refreshKey}`)
      : "";
    if (receptorFingerprint && receptorModelRef.current?.fingerprint !== receptorFingerprint) {
      if (receptorModelRef.current) viewer.removeModel(receptorModelRef.current.model);
      const model = viewer.addModel(structures.receptor!.content, structures.receptor!.format);
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
    if (receptorModelRef.current) {
      const receptorModel = receptorModelRef.current.model as unknown as {
        setStyle: (selection: Record<string, unknown>, style: Record<string, unknown>, add?: boolean) => void;
        setClickable?: (
          selection: Record<string, unknown>,
          clickable: boolean,
          callback?: (atom: { chain?: string; resi?: number | string; icode?: string; resn?: string }) => void,
        ) => void;
      };
      receptorModel.setStyle({}, {
        cartoon: { color: "spectrum", opacity: 0.74 },
        stick: { radius: 0.1, colorscheme: "Jmol" },
      });
      for (const selector of selectedResidues) {
        const [chain, residueNumber] = selector.split(":");
        if (!chain || !residueNumber) continue;
        receptorModel.setStyle(
          { chain, resi: Number.isFinite(Number(residueNumber)) ? Number(residueNumber) : residueNumber },
          { stick: { radius: 0.3, color: "#f4c95d" }, sphere: { scale: 0.22, color: "#f4c95d" } },
          true,
        );
      }
      receptorModel.setClickable?.({}, residueSelectionActive, residueSelectionActive ? (atom) => {
        const chain = String(atom.chain || "").trim();
        const residue = String(atom.resi ?? "").trim();
        if (!chain || !residue) {
          setMessage("该原子缺少链 ID 或残基编号，不能作为柔性残基选择。");
          return;
        }
        const selector = `${chain}:${residue}${atom.icode ? `:${String(atom.icode).trim()}` : ""}`;
        if (!selectedResidues.includes(selector) && selectedResidues.length >= 8) {
          setMessage("最多选择 8 个柔性残基。");
          return;
        }
        onResidueSelect?.(selector);
        setMessage(`已点选 ${selector}${atom.resn ? ` ${atom.resn}` : ""}；可继续点选或进入柔性准备。`);
      } : undefined);
    }

    const ligandFingerprint = structures.ligand?.ok
      ? structureFingerprint(structures.ligand.content, structures.ligand.format, `${structures.ligand.relative_path}:${refreshKey}`)
      : "";
    if (ligandFingerprint && ligandModelRef.current?.fingerprint !== ligandFingerprint) {
      if (ligandModelRef.current) viewer.removeModel(ligandModelRef.current.model);
      const model = viewer.addModel(structures.ligand!.content, structures.ligand!.format);
      model.setStyle({}, { stick: { radius: 0.25, colorscheme: "greenCarbon" }, sphere: { scale: 0.22 } });
      ligandModelRef.current = { fingerprint: ligandFingerprint, model };
    } else if (!ligandFingerprint && ligandModelRef.current) {
      viewer.removeModel(ligandModelRef.current.model);
      ligandModelRef.current = null;
    }
    if (showLigand) ligandModelRef.current?.model.show();
    else ligandModelRef.current?.model.hide();

    viewer.removeAllShapes();
    viewer.removeAllLabels();
    const boxVisualization = buildBoxVisualization(boxRef.current);
    if (showBox && boxVisualization) {
      addBoxOverlay(viewer, boxVisualization, boxLineThickness);
    }

    if (showAxes) addOrientationAxes(viewer, boxVisualization, axisSpacingScales[axisSpacing]);
    if (previousView) {
      (viewer as unknown as { setView?: (view: unknown) => void }).setView?.(previousView);
    } else {
      viewer.zoomTo();
    }
    viewer.render();
  }, [axisSpacing, boxLineThickness, ensureViewer, onResidueSelect, refreshKey, residueSelectionActive, selectedResidues, showAxes, showBox, showLigand, showReceptor, structures]);

  const refreshBoxOverlay = useCallback(async () => {
    const sceneGeneration = sceneGenerationRef.current;
    const viewer = await ensureViewer();
    if (!viewer || sceneGeneration !== sceneGenerationRef.current) return;
    viewer.removeAllShapes();
    viewer.removeAllLabels();
    const boxVisualization = buildBoxVisualization(boxRef.current);
    if (showBox && boxVisualization) addBoxOverlay(viewer, boxVisualization, boxLineThickness);
    if (showAxes) addOrientationAxes(viewer, boxVisualization, axisSpacingScales[axisSpacing]);
    viewer.render();
  }, [ensureViewer, showBox, showAxes, boxLineThickness, axisSpacing]);

  useEffect(() => {
    if (identityRef.current === projectDir) return;
    identityRef.current = projectDir;
    sceneGenerationRef.current += 1;
    loadGenerationRef.current += 1;
    hasFitRef.current = false;
    setStructures({ receptor: null, ligand: null });
    setMessage("正在读取受体、配体与搜索范围…");
    setIsSpinning(false);
    const viewer = viewerRef.current;
    (viewer as unknown as { spin?: (axis: string | boolean, speed?: number) => void } | null)?.spin?.(false);
    if (viewer && receptorModelRef.current) viewer.removeModel(receptorModelRef.current.model);
    if (viewer && ligandModelRef.current) viewer.removeModel(ligandModelRef.current.model);
    receptorModelRef.current = null;
    ligandModelRef.current = null;
    viewer?.removeAllShapes();
    viewer?.removeAllLabels();
    viewer?.render();
  }, [projectDir]);

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
    const generation = ++loadGenerationRef.current;
    async function loadPreview() {
      setMessage("正在读取受体、配体与搜索范围…");
      try {
        const [receptorPayload, ligandPayload] = await Promise.all([
          invoke<string>("load_structure_for_viewer", { projectDir, fileKind: "receptor_prepared" }),
          invoke<string>("load_structure_for_viewer", { projectDir, fileKind: "ligand_prepared" }),
        ]);
        if (cancelled || generation !== loadGenerationRef.current) return;
        const receptor = parseStructure(receptorPayload);
        const ligand = parseStructure(ligandPayload);
        setStructures({ receptor, ligand });
        if (receptor.ok && ligand.ok) setMessage("受体、配体与搜索范围已加载");
        else if (receptor.ok || ligand.ok) setMessage("部分结构可显示；请查看运行前检查");
        else setMessage("尚无可显示的 prepared PDBQT");
      } catch (error) {
        if (!cancelled && generation === loadGenerationRef.current) {
          setMessage(error instanceof Error ? error.message : "结构预览加载失败");
        }
      }
    }
    void loadPreview();
    return () => {
      cancelled = true;
    };
  }, [projectDir, refreshKey]);

  useEffect(() => {
    const shouldFit = !hasFitRef.current && Boolean(structures.receptor?.ok || structures.ligand?.ok);
    const sceneGeneration = sceneGenerationRef.current;
    void renderScene(shouldFit)
      .then(() => {
        if (shouldFit && sceneGeneration === sceneGenerationRef.current) hasFitRef.current = true;
      })
      .catch((error) => setMessage(error instanceof Error ? error.message : "3D 场景渲染失败"));
  }, [renderScene, structures]);

  useEffect(() => {
    boxRef.current = box;
    void refreshBoxOverlay().catch((error) => {
      setMessage(error instanceof Error ? error.message : "搜索范围预览刷新失败");
    });
  }, [box, refreshBoxOverlay]);

  useEffect(() => {
    if (fitRequestKey <= 0) return;
    void renderScene(true).catch((error) => {
      setMessage(error instanceof Error ? error.message : "无法重新适配结构与搜索范围");
    });
  }, [fitRequestKey, renderScene]);

  // 全屏变化时的自适应调整
  useEffect(() => {
    if (residueSelectionActive) setShowReceptor(true);
  }, [residueSelectionActive]);

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
      if (event.key === "Escape") closeFullscreen();
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [closeFullscreen, isFullscreen]);

  useEffect(() => () => {
    const viewer = viewerRef.current as unknown as { spin?: (axis: string | boolean, speed?: number) => void } | null;
    viewer?.spin?.(false);
    viewerRef.current?.clear();
    viewerRef.current = null;
    viewerInitRef.current = null;
    receptorModelRef.current = null;
    ligandModelRef.current = null;
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
    if (residueSelectionActive) {
      finishResidueSelection();
      return;
    }
    setManualFullscreen((current) => !current);
  };

  return (
    <section
      className={`run-preview ${isFullscreen ? "is-fullscreen" : ""} ${showFullscreenInspector ? "has-fullscreen-inspector" : ""} ${residueSelectionActive ? "is-residue-selection" : ""}`.trim()}
      aria-label="运行前结构预览"
    >
      <div
        className={`run-preview-canvas ${residueSelectionActive ? "is-residue-picking" : ""}`.trim()}
        data-wheel-bound={wheelBinding || undefined}
        ref={containerRef}
        tabIndex={0}
        aria-label={
          wheelBinding
            ? `受体、配体和搜索范围三维视图；滚轮当前调整${runBoxFieldLabels[wheelBinding]}`
            : "受体、配体和搜索范围三维视图；滚轮缩放"
        }
      />

      <div className="run-preview-toolbar" aria-label="3D 视图工具">
        <button type="button" onClick={() => zoom(1.18)} title="放大" aria-label="放大结构">
          <MagnifyingGlassPlus size={18} />
        </button>
        <button type="button" onClick={() => zoom(0.84)} title="缩小" aria-label="缩小结构">
          <MagnifyingGlassMinus size={18} />
        </button>
        <button type="button" onClick={() => void renderScene(true)} title="适应窗口" aria-label="让结构适应窗口">
          <ArrowsOut size={18} />
        </button>
        <button type="button" onClick={toggleSpin} title={isSpinning ? "停止旋转" : "自动旋转"} aria-label={isSpinning ? "停止自动旋转" : "开始自动旋转"}>
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
        <button
          type="button"
          onClick={toggleFullscreen}
          title={residueSelectionActive ? "选择完成" : isFullscreen ? "关闭全屏" : "全屏查看"}
          aria-label={residueSelectionActive ? "完成柔性残基点选" : isFullscreen ? "关闭全屏" : "全屏查看"}
        >
          {residueSelectionActive ? <CheckCircle size={18} /> : isFullscreen ? <CornersIn size={18} /> : <CornersOut size={18} />}
        </button>
      </div>

      {isFullscreen ? (
        <>
          {showFullscreenInspector ? fullscreenInspector : null}
          {residueSelectionActive ? (
            <div className="run-preview-fullscreen-controls run-residue-selection-controls" aria-live="polite">
              <span>已选择 <strong>{selectedResidues.length}</strong>/8</span>
              <button type="button" onClick={finishResidueSelection} className="primary-button compact-btn fullscreen-close-btn">
                <CheckCircle size={15} /> 选择完成
              </button>
            </div>
          ) : (
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
          )}
        </>
      ) : (
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
      )}
    </section>
  );
}
