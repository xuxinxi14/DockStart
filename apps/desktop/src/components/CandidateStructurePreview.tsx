import { useCallback, useEffect, useRef, useState } from "react";
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
  const modelRef = useRef<{ fingerprint: string; model: ThreeDmolModel } | null>(null);
  const [message, setMessage] = useState(content ? "正在绘制候选结构…" : "选择候选后可进行 3D 预览");

  const ensureViewer = useCallback(async () => {
    if (viewerRef.current) return viewerRef.current;
    if (!containerRef.current) return null;
    if (!viewerInitRef.current) {
      const container = containerRef.current;
      viewerInitRef.current = load3Dmol().then(($3Dmol) => {
        if (!container.isConnected) return null;
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
    const normalizedFormat = format.trim().toLowerCase();
    const fingerprint = content
      ? structureFingerprint(content, normalizedFormat, label)
      : "";

    void ensureViewer().then((viewer) => {
      if (cancelled || !viewer) return;
      if (!fingerprint) {
        if (modelRef.current) viewer.removeModel(modelRef.current.model);
        modelRef.current = null;
        viewer.render();
        setMessage("选择候选后可进行 3D 预览");
        return;
      }
      if (modelRef.current?.fingerprint === fingerprint) return;

      try {
        if (modelRef.current) viewer.removeModel(modelRef.current.model);
        const model = viewer.addModel(content, normalizedFormat);
        if (LIGAND_FORMATS.has(normalizedFormat)) {
          model.setStyle({}, {
            stick: { radius: 0.25, colorscheme: "greenCarbon" },
            sphere: { scale: 0.22 },
          });
        } else {
          model.setStyle({}, {
            cartoon: { color: "spectrum", opacity: 0.82 },
            stick: { radius: 0.08, colorscheme: "Jmol" },
          });
        }
        modelRef.current = { fingerprint, model };
        viewer.zoomTo();
        viewer.render();
        setMessage(`${label} · 临时预览`);
      } catch (error) {
        modelRef.current = null;
        viewer.clear();
        viewer.render();
        setMessage(error instanceof Error ? `3D 预览失败：${error.message}` : "3D 预览失败，请选择其他候选");
      }
    });

    return () => {
      cancelled = true;
    };
  }, [content, ensureViewer, format, label]);

  useEffect(() => () => {
    viewerRef.current?.clear();
    viewerRef.current = null;
    viewerInitRef.current = null;
    modelRef.current = null;
    containerRef.current?.replaceChildren();
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
      <div className="run-preview-canvas" ref={containerRef} tabIndex={0} />
      <div className="run-preview-legend" aria-live="polite">
        <span><i className={`run-preview-dot ${isLigand ? "ligand" : "receptor"}`} />候选结构</span>
        <strong>{message}</strong>
      </div>
    </div>
  );
}
