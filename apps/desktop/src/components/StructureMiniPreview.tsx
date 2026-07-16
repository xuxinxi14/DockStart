import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ViewerStructureResult } from "../types";
import {
  load3Dmol,
  structureFingerprint,
  type ThreeDmolModel,
  type ThreeDmolViewer,
} from "./threeDmolLoader";

type StructureMiniPreviewProps = {
  projectDir: string;
  fileKind: "receptor_prepared" | "ligand_prepared";
  label: string;
  refreshKey?: number;
};

function parseStructure(rawPayload: string): ViewerStructureResult {
  return JSON.parse(rawPayload) as ViewerStructureResult;
}

export default function StructureMiniPreview({
  projectDir,
  fileKind,
  label,
  refreshKey = 0,
}: StructureMiniPreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ThreeDmolViewer | null>(null);
  const viewerInitRef = useRef<Promise<ThreeDmolViewer | null> | null>(null);
  const modelRef = useRef<{ fingerprint: string; model: ThreeDmolModel } | null>(null);
  const identityRef = useRef("");
  const sceneGenerationRef = useRef(0);
  const loadGenerationRef = useRef(0);
  const [structure, setStructure] = useState<ViewerStructureResult | null>(null);
  const [message, setMessage] = useState("正在读取结构…");

  const ensureViewer = useCallback(async () => {
    if (viewerRef.current) return viewerRef.current;
    if (!containerRef.current) return null;
    if (!viewerInitRef.current) {
      const container = containerRef.current;
      viewerInitRef.current = load3Dmol().then(($3Dmol) => {
        if (!container.isConnected) return null;
        const background = getComputedStyle(document.documentElement).getPropertyValue("--ds-viewer-bg").trim();
        viewerRef.current = $3Dmol.createViewer(container, {
          backgroundColor: background || "#071f35",
          antialias: true,
        });
        return viewerRef.current;
      });
    }
    return viewerInitRef.current;
  }, []);

  useEffect(() => {
    const identity = `${projectDir}|${fileKind}`;
    if (identityRef.current === identity) return;
    identityRef.current = identity;
    sceneGenerationRef.current += 1;
    loadGenerationRef.current += 1;
    setStructure(null);
    setMessage("正在读取结构…");
    const viewer = viewerRef.current;
    (viewer as unknown as { spin?: (axis: string | boolean, speed?: number) => void } | null)?.spin?.(false);
    if (viewer && modelRef.current) viewer.removeModel(modelRef.current.model);
    modelRef.current = null;
    viewer?.removeAllShapes();
    viewer?.removeAllLabels();
    viewer?.render();
  }, [fileKind, projectDir]);

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
    const generation = ++loadGenerationRef.current;
    async function loadStructure() {
      setMessage("正在读取结构…");
      try {
        const rawPayload = await invoke<string>("load_structure_for_viewer", { projectDir, fileKind });
        if (cancelled || generation !== loadGenerationRef.current) return;
        const parsed = parseStructure(rawPayload);
        setStructure(parsed);
        setMessage(parsed.ok ? "结构已加载" : parsed.message || "暂无可预览结构");
      } catch (error) {
        if (!cancelled && generation === loadGenerationRef.current) {
          setMessage(error instanceof Error ? error.message : "结构读取失败");
        }
      }
    }
    void loadStructure();
    return () => {
      cancelled = true;
    };
  }, [fileKind, projectDir, refreshKey]);

  useEffect(() => {
    const sceneGeneration = sceneGenerationRef.current;
    void ensureViewer().then((viewer) => {
      if (!viewer || sceneGeneration !== sceneGenerationRef.current) return;
      const fingerprint = structure?.ok
        ? structureFingerprint(structure.content, structure.format, `${structure.relative_path}:${refreshKey}`)
        : "";
      if (fingerprint && modelRef.current?.fingerprint !== fingerprint) {
        if (modelRef.current) viewer.removeModel(modelRef.current.model);
        const model = viewer.addModel(structure!.content, structure!.format);
        if (fileKind === "receptor_prepared") {
          model.setStyle({}, {
            cartoon: { color: "#79a9cf", opacity: 0.86 },
            stick: { radius: 0.08, colorscheme: "Jmol" },
          });
        } else {
          model.setStyle({}, {
            stick: { radius: 0.25, colorscheme: "greenCarbon" },
            sphere: { scale: 0.22 },
          });
        }
        modelRef.current = { fingerprint, model };
        viewer.zoomTo();
      } else if (!fingerprint && modelRef.current) {
        viewer.removeModel(modelRef.current.model);
        modelRef.current = null;
      }
      viewer.render();
    });
  }, [ensureViewer, fileKind, refreshKey, structure]);

  useEffect(() => () => {
    const viewer = viewerRef.current as unknown as { spin?: (axis: string | boolean, speed?: number) => void } | null;
    viewer?.spin?.(false);
    viewerRef.current?.clear();
    viewerRef.current = null;
    viewerInitRef.current = null;
    modelRef.current = null;
    containerRef.current?.replaceChildren();
  }, []);

  return (
    <div className="structure-mini-preview" aria-label={`${label} 3D 预览`}>
      <div className="structure-mini-preview-canvas" ref={containerRef} />
      {!structure?.ok ? <span>{message}</span> : null}
    </div>
  );
}
