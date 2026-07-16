import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowsOut, MagnifyingGlassMinus, MagnifyingGlassPlus } from "@phosphor-icons/react";
import {
  load3Dmol,
  structureFingerprint,
  type ThreeDmolModel,
  type ThreeDmolViewer,
} from "./threeDmolLoader";

type CandidateStructurePreviewProps = {
  content: string;
  format: string;
  label: string;
  className?: string;
};

const LIGAND_FORMATS = new Set(["sdf", "mol", "mol2"]);

function releaseViewerSurface(container: HTMLDivElement | null): void {
  if (!container) return;
  container.replaceChildren();
}

/**
 * Read-only preview for a remote search candidate.
 *
 * The caller owns acquisition and selection state. This component never
 * invokes the backend and therefore cannot download a default result or
 * mutate the current project.
 */
export default function CandidateStructurePreview({
  content,
  format,
  label,
  className = "",
}: CandidateStructurePreviewProps) {
  const isLigand = LIGAND_FORMATS.has(format.trim().toLowerCase());
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ThreeDmolViewer | null>(null);
  const viewerInitRef = useRef<Promise<ThreeDmolViewer | null> | null>(null);
  const viewerGenerationRef = useRef(0);
  const modelRef = useRef<{ fingerprint: string; model: ThreeDmolModel } | null>(null);
  const [message, setMessage] = useState(content ? "正在绘制候选结构…" : "选择候选后可进行 3D 预览");

  const ensureViewer = useCallback(async () => {
    if (viewerRef.current) return viewerRef.current;
    if (!containerRef.current) return null;
    if (!viewerInitRef.current) {
      const container = containerRef.current;
      const generation = viewerGenerationRef.current;
      viewerInitRef.current = load3Dmol().then(($3Dmol) => {
        if (!container.isConnected || viewerGenerationRef.current !== generation) return null;
        const background = getComputedStyle(document.documentElement).getPropertyValue("--ds-viewer-bg").trim();
        viewerRef.current = $3Dmol.createViewer(container, {
          backgroundColor: background || "#031A2D",
          antialias: true,
        });
        return viewerRef.current;
      });
    }
    return viewerInitRef.current;
  }, []);

  const fit = useCallback(() => {
    viewerRef.current?.zoomTo();
    viewerRef.current?.render();
  }, []);

  const zoom = useCallback((factor: number) => {
    viewerRef.current?.zoom(factor);
    viewerRef.current?.render();
  }, []);

  useEffect(() => {
    let observer: ResizeObserver | null = null;
    let cancelled = false;
    const generation = viewerGenerationRef.current;
    const container = containerRef.current;
    void ensureViewer().then((viewer) => {
      if (
        cancelled
        || !viewer
        || !container
        || viewerGenerationRef.current !== generation
        || !container.isConnected
      ) return;
      observer = new ResizeObserver(() => {
        if (
          cancelled
          || viewerGenerationRef.current !== generation
          || !container.isConnected
        ) return;
        viewer.resize();
        viewer.render();
      });
      observer.observe(container);
    });
    return () => {
      cancelled = true;
      observer?.disconnect();
    };
  }, [ensureViewer]);

  useEffect(() => {
    let cancelled = false;
    let animationFrame = 0;
    let drawTimer = 0;
    const generation = viewerGenerationRef.current;
    const container = containerRef.current;
    const normalizedFormat = format.trim().toLowerCase();
    const fingerprint = content
      ? structureFingerprint(content, normalizedFormat, label)
      : "";

    void ensureViewer().then((viewer) => {
      if (
        cancelled
        || !viewer
        || !container
        || viewerGenerationRef.current !== generation
        || !container.isConnected
      ) return;
      // Give the browser one paint and one task boundary before 3Dmol parses
      // the candidate. A navigation click queued while the preview shell is
      // appearing can therefore unmount the page before expensive drawing.
      animationFrame = window.requestAnimationFrame(() => {
        if (
          cancelled
          || viewerGenerationRef.current !== generation
          || !container.isConnected
        ) return;
        drawTimer = window.setTimeout(() => {
          if (
            cancelled
            || viewerGenerationRef.current !== generation
            || !container.isConnected
          ) return;
          if (!fingerprint) {
            if (modelRef.current) viewer.removeModel(modelRef.current.model);
            modelRef.current = null;
            viewer.render();
            setMessage("选择候选后可进行 3D 预览");
            return;
          }
          if (modelRef.current?.fingerprint === fingerprint) return;

          const previousModel = modelRef.current;
          let model: ThreeDmolModel | null = null;
          try {
            model = viewer.addModel(content, normalizedFormat);
            if (LIGAND_FORMATS.has(normalizedFormat)) {
              model.setStyle({}, {
                stick: { radius: 0.25, colorscheme: "greenCarbon" },
                sphere: { scale: 0.22 },
              });
            } else {
              // Candidate preview is for identification, so keep large
              // receptors lightweight instead of drawing sticks for every atom.
              model.setStyle({}, {
                cartoon: { color: "spectrum", opacity: 0.86 },
              });
              model.setStyle({ hetflag: true }, {
                stick: { radius: 0.12, colorscheme: "Jmol" },
              });
            }
            if (previousModel) viewer.removeModel(previousModel.model);
            modelRef.current = { fingerprint, model };
            viewer.zoomTo();
            viewer.render();
            setMessage(`${label} · 临时预览`);
          } catch (error) {
            if (model) viewer.removeModel(model);
            modelRef.current = previousModel;
            viewer.render();
            setMessage(error instanceof Error ? `3D 预览失败：${error.message}` : "3D 预览失败，请选择其他候选");
          }
        }, 0);
      });
    });

    return () => {
      cancelled = true;
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      if (drawTimer) window.clearTimeout(drawTimer);
    };
  }, [content, ensureViewer, format, label]);

  useLayoutEffect(() => () => {
    // `GLViewer.clear()` ends by rendering the emptied scene. Doing that
    // synchronously during a React route unmount can stall navigation for a
    // large raw PDB/mmCIF candidate. Detaching the canvas and dropping the
    // viewer references lets WebView release the private WebGL scene without
    // forcing one last main-thread render for a page that is already leaving.
    const viewer = viewerRef.current as unknown as {
      spin?: (axis: string | boolean, speed?: number) => void;
    } | null;
    viewerGenerationRef.current += 1;
    viewer?.spin?.(false);
    releaseViewerSurface(containerRef.current);
    viewerRef.current = null;
    viewerInitRef.current = null;
    modelRef.current = null;
  }, []);

  return (
    <div className={`run-preview candidate-structure-preview ${className}`.trim()} aria-label={`${label} 3D 候选预览`}>
      <div className="run-preview-toolbar" aria-label="候选 3D 视图工具">
        <button type="button" onClick={() => zoom(1.18)} disabled={!content} title="放大" aria-label="放大候选结构">
          <MagnifyingGlassPlus size={18} />
        </button>
        <button type="button" onClick={() => zoom(0.84)} disabled={!content} title="缩小" aria-label="缩小候选结构">
          <MagnifyingGlassMinus size={18} />
        </button>
        <button type="button" onClick={fit} disabled={!content} title="适应窗口" aria-label="候选结构适应窗口">
          <ArrowsOut size={18} />
        </button>
      </div>
      <div
        aria-label={`${label} 的只读三维结构视图。可使用上方按钮缩放或适应窗口。`}
        className="run-preview-canvas"
        ref={containerRef}
        role="img"
      />
      <div className="run-preview-legend" aria-live="polite">
        <span><i className={`run-preview-dot ${isLigand ? "ligand" : "receptor"}`} />候选结构</span>
        <strong>{message}</strong>
      </div>
    </div>
  );
}
