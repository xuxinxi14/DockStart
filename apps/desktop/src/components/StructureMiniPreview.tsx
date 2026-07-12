import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import * as $3Dmol from "3dmol";
import type { ViewerStructureResult } from "../types";

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
  const viewerRef = useRef<ReturnType<typeof $3Dmol.createViewer> | null>(null);
  const [structure, setStructure] = useState<ViewerStructureResult | null>(null);
  const [message, setMessage] = useState("正在读取结构…");

  const ensureViewer = useCallback(() => {
    if (viewerRef.current) return viewerRef.current;
    if (!containerRef.current) return null;
    const background = getComputedStyle(document.documentElement).getPropertyValue("--ds-viewer-bg").trim();
    viewerRef.current = $3Dmol.createViewer(containerRef.current, {
      backgroundColor: background || "#071f35",
      antialias: true,
    });
    return viewerRef.current;
  }, []);

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
    async function loadStructure() {
      setMessage("正在读取结构…");
      try {
        const rawPayload = await invoke<string>("load_structure_for_viewer", { projectDir, fileKind });
        if (cancelled) return;
        const parsed = parseStructure(rawPayload);
        setStructure(parsed);
        setMessage(parsed.ok ? "结构已加载" : parsed.message || "暂无可预览结构");
      } catch (error) {
        if (!cancelled) setMessage(error instanceof Error ? error.message : "结构读取失败");
      }
    }
    void loadStructure();
    return () => {
      cancelled = true;
    };
  }, [fileKind, projectDir, refreshKey]);

  useEffect(() => {
    const viewer = ensureViewer();
    if (!viewer) return;
    viewer.clear();
    if (structure?.ok) {
      const model = viewer.addModel(structure.content, structure.format);
      if (fileKind === "receptor_prepared") {
        model.setStyle({}, {
          cartoon: { color: "#79a9cf", opacity: 0.86 },
          stick: { radius: 0.08, colorscheme: "Jmol" },
        });
      } else {
        model.setStyle({}, {
          stick: { radius: 0.26, colorscheme: "greenCarbon" },
          sphere: { scale: 0.22 },
        });
      }
      viewer.zoomTo();
      viewer.render();
    }
  }, [ensureViewer, fileKind, structure]);

  useEffect(() => () => {
    viewerRef.current?.clear();
    viewerRef.current = null;
    containerRef.current?.replaceChildren();
  }, []);

  return (
    <div className="structure-mini-preview" aria-label={`${label} 3D 预览`}>
      <div className="structure-mini-preview-canvas" ref={containerRef} />
      {!structure?.ok ? <span>{message}</span> : null}
    </div>
  );
}
