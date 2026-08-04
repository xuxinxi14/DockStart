import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { MacrocycleBond, ViewerStructureResult } from "../types";
import {
  load3Dmol,
  structureFingerprint,
  type ThreeDmolModel,
  type ThreeDmolViewer,
} from "./threeDmolLoader";

type StructureMiniPreviewProps = {
  projectDir: string;
  fileKind:
    | "receptor_raw"
    | "ligand_raw"
    | "receptor_prepared"
    | "ligand_prepared";
  label: string;
  refreshKey?: number;
  highlightBonds?: MacrocycleBond[];
  screeningCandidate?: {
    id: string;
    revisionSha256: string;
  };
};

const EMPTY_HIGHLIGHT_BONDS: MacrocycleBond[] = [];

function parseStructure(rawPayload: string): ViewerStructureResult {
  return JSON.parse(rawPayload) as ViewerStructureResult;
}

export default function StructureMiniPreview({
  projectDir,
  fileKind,
  label,
  refreshKey = 0,
  highlightBonds = EMPTY_HIGHLIGHT_BONDS,
  screeningCandidate,
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
    const identity = `${projectDir}|${fileKind}|${screeningCandidate?.id ?? ""}|${screeningCandidate?.revisionSha256 ?? ""}`;
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
  }, [fileKind, projectDir, screeningCandidate?.id, screeningCandidate?.revisionSha256]);

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
        const rawPayload = screeningCandidate
          ? await invoke<string>("load_screening_candidate_for_viewer", {
              projectDir,
              candidateId: screeningCandidate.id,
              expectedRevisionSha256: screeningCandidate.revisionSha256,
            })
          : await invoke<string>("load_structure_for_viewer", { projectDir, fileKind });
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
  }, [fileKind, projectDir, refreshKey, screeningCandidate?.id, screeningCandidate?.revisionSha256]);

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
        if (fileKind.startsWith("receptor")) {
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
      const activeModel = modelRef.current?.model;
      viewer.removeAllShapes();
      viewer.removeAllLabels();
      if (activeModel) {
        if (fileKind.startsWith("receptor")) {
          activeModel.setStyle({}, {
            cartoon: { color: "#79a9cf", opacity: 0.86 },
            stick: { radius: 0.08, colorscheme: "Jmol" },
          });
        } else {
          activeModel.setStyle({}, {
            stick: { radius: 0.25, colorscheme: "greenCarbon" },
            sphere: { scale: 0.22 },
          });
        }
      }
      if (activeModel && highlightBonds.length) {
        const atomIndices = Array.from(new Set(
          highlightBonds.flatMap((bond) => bond.atom_indices_zero_based),
        )).filter((value) => Number.isInteger(value) && value >= 0);
        if (atomIndices.length) {
          activeModel.setStyle({ index: atomIndices }, {
            stick: { radius: 0.34, color: "#f5b942" },
            sphere: { scale: 0.34, color: "#f5b942" },
          });
        }
        const shapeViewer = viewer as unknown as {
          addCylinder?: (spec: Record<string, unknown>) => void;
        };
        const labeledAtoms = new Set<number>();
        for (const bond of highlightBonds) {
          const [left, right] = bond.endpoints;
          const start = left?.coordinates;
          const end = right?.coordinates;
          if (
            start?.length === 3
            && end?.length === 3
            && [...start, ...end].every((value) => Number.isFinite(value))
          ) {
            shapeViewer.addCylinder?.({
              start: { x: start[0], y: start[1], z: start[2] },
              end: { x: end[0], y: end[1], z: end[2] },
              radius: 0.1,
              color: "#f5b942",
              opacity: 0.95,
            });
          }
          for (const endpoint of [left, right]) {
            if (
              !endpoint
              || labeledAtoms.has(endpoint.index_zero_based)
              || endpoint.coordinates.length !== 3
            ) continue;
            labeledAtoms.add(endpoint.index_zero_based);
            viewer.addLabel(
              `#${endpoint.number_one_based} ${endpoint.name}`,
              {
                position: {
                  x: endpoint.coordinates[0],
                  y: endpoint.coordinates[1],
                  z: endpoint.coordinates[2],
                },
                fontColor: "#fff6d8",
                backgroundColor: "#6e4b11",
                backgroundOpacity: 0.82,
                borderColor: "#f5b942",
                borderThickness: 1,
                fontSize: 11,
                inFront: true,
              },
            );
          }
        }
      }
      viewer.render();
    });
  }, [ensureViewer, fileKind, highlightBonds, refreshKey, structure]);

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
