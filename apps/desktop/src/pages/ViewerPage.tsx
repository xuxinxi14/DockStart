import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import * as $3Dmol from "3dmol";
import type {
  BoxVisualizationPayload,
  BoxVisualizationResponse,
  DockingPoseListResponse,
  DockingPoseSummary,
  DockStartProject,
  ViewerFileKind,
  ViewerFileStatusResponse,
  ViewerStructureResult,
} from "../types";
import CommandResultPanel from "../components/CommandResultPanel";
import PageHeader from "../components/PageHeader";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";

type ViewerPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
};

type ViewerLayerKey = ViewerFileKind | "pose";

type LoadedViewerLayer = {
  key: ViewerLayerKey;
  label: string;
  structure: ViewerStructureResult;
  visible: boolean;
};

type Coordinate = { x: number; y: number; z: number };

type CoordinateBounds = {
  atomCount: number;
  min: Coordinate;
  max: Coordinate;
  center: Coordinate;
  size: Coordinate;
};

const fileKindOptions: Array<{ value: ViewerFileKind; label: string; description: string }> = [
  { value: "receptor_raw", label: "受体原始结构", description: "项目中记录的受体原始文件" },
  { value: "ligand_raw", label: "配体原始结构", description: "项目中记录的配体原始文件" },
  { value: "receptor_prepared", label: "受体 Vina 输入", description: "prepared/receptor.pdbqt" },
  { value: "ligand_prepared", label: "配体 Vina 输入", description: "prepared/ligand.pdbqt" },
  { value: "docking_output", label: "对接输出", description: "最近对接运行的 out.pdbqt" },
];

const layerOrder: ViewerLayerKey[] = [
  "receptor_raw",
  "receptor_prepared",
  "ligand_raw",
  "ligand_prepared",
  "docking_output",
  "pose",
];

const layerLabels: Record<ViewerLayerKey, string> = {
  receptor_raw: "受体原始结构",
  receptor_prepared: "受体 Vina 输入",
  ligand_raw: "配体原始结构",
  ligand_prepared: "配体 Vina 输入",
  docking_output: "对接输出",
  pose: "当前对接构象",
};

const viewerFormats = new Set(["pdb", "pdbqt", "cif", "sdf", "mol", "mol2"]);
const fitMarginAngstrom = 8;
const minBoxDimension = 8;

function parseViewerStatus(rawPayload: string): ViewerFileStatusResponse {
  return JSON.parse(rawPayload) as ViewerFileStatusResponse;
}

function parseViewerStructure(rawPayload: string): ViewerStructureResult {
  return JSON.parse(rawPayload) as ViewerStructureResult;
}

function parseBoxVisualization(rawPayload: string): BoxVisualizationResponse {
  return JSON.parse(rawPayload) as BoxVisualizationResponse;
}

function parseDockingPoseList(rawPayload: string): DockingPoseListResponse {
  return JSON.parse(rawPayload) as DockingPoseListResponse;
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) {
    return "0 B";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function displayStatus(result: ViewerStructureResult | undefined): string {
  if (!result) {
    return "未检测";
  }
  if (result.ok) {
    return "可读取";
  }
  return result.message || result.error?.message || "不可读取";
}

function viewerStatusTone(result: ViewerStructureResult | undefined): "ok" | "warning" | "error" | "muted" {
  if (!result) {
    return "muted";
  }
  if (result.ok) {
    return "ok";
  }
  if (result.error) {
    return "error";
  }
  return result.exists ? "warning" : "muted";
}

function buildLocalBoxVisualization(box: DockStartProject["box"]): BoxVisualizationPayload | null {
  const { center_x, center_y, center_z, size_x, size_y, size_z } = box;
  if (![center_x, center_y, center_z, size_x, size_y, size_z].every(Number.isFinite)) {
    return null;
  }
  if (size_x <= 0 || size_y <= 0 || size_z <= 0) {
    return null;
  }
  const min = {
    x: center_x - size_x / 2,
    y: center_y - size_y / 2,
    z: center_z - size_z / 2,
  };
  const max = {
    x: center_x + size_x / 2,
    y: center_y + size_y / 2,
    z: center_z + size_z / 2,
  };
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
      color: "cyan",
      alpha: 0.12,
      wireframe: true,
    },
  };
}

function localBoxWarnings(box: DockStartProject["box"]): string[] {
  return [box.size_x, box.size_y, box.size_z].some((value) => value > 60)
    ? ["对接箱体尺寸较大，可能导致搜索变慢或结果不稳定，请确认是否覆盖了合理结合区域。"]
    : [];
}

function latestRunId(project: DockStartProject): string {
  for (const run of [...project.runs].reverse()) {
    const value = run.run_id;
    if (typeof value === "string" && value) {
      return value;
    }
  }
  return "";
}

function boxFieldLabel(key: keyof DockStartProject["box"]): string {
  const labels: Record<keyof DockStartProject["box"], string> = {
    center_x: "中心 X",
    center_y: "中心 Y",
    center_z: "中心 Z",
    size_x: "尺寸 X",
    size_y: "尺寸 Y",
    size_z: "尺寸 Z",
  };
  return labels[key];
}

function roundBoxValue(value: number): number {
  return Number(value.toFixed(2));
}

function coordinateBounds(points: Coordinate[]): CoordinateBounds | null {
  if (!points.length) {
    return null;
  }
  const min = points.reduce(
    (acc, point) => ({
      x: Math.min(acc.x, point.x),
      y: Math.min(acc.y, point.y),
      z: Math.min(acc.z, point.z),
    }),
    { x: Number.POSITIVE_INFINITY, y: Number.POSITIVE_INFINITY, z: Number.POSITIVE_INFINITY },
  );
  const max = points.reduce(
    (acc, point) => ({
      x: Math.max(acc.x, point.x),
      y: Math.max(acc.y, point.y),
      z: Math.max(acc.z, point.z),
    }),
    { x: Number.NEGATIVE_INFINITY, y: Number.NEGATIVE_INFINITY, z: Number.NEGATIVE_INFINITY },
  );
  const center = {
    x: (min.x + max.x) / 2,
    y: (min.y + max.y) / 2,
    z: (min.z + max.z) / 2,
  };
  return {
    atomCount: points.length,
    min,
    max,
    center,
    size: {
      x: max.x - min.x,
      y: max.y - min.y,
      z: max.z - min.z,
    },
  };
}

function parsePdbLikeCoordinates(content: string): Coordinate[] {
  const points: Coordinate[] = [];
  for (const line of content.split(/\r?\n/)) {
    if (!line.startsWith("ATOM") && !line.startsWith("HETATM")) {
      continue;
    }
    const fromColumns = {
      x: Number.parseFloat(line.slice(30, 38)),
      y: Number.parseFloat(line.slice(38, 46)),
      z: Number.parseFloat(line.slice(46, 54)),
    };
    if ([fromColumns.x, fromColumns.y, fromColumns.z].every(Number.isFinite)) {
      points.push(fromColumns);
      continue;
    }
    const fields = line.trim().split(/\s+/);
    const fallback = {
      x: Number.parseFloat(fields[6]),
      y: Number.parseFloat(fields[7]),
      z: Number.parseFloat(fields[8]),
    };
    if ([fallback.x, fallback.y, fallback.z].every(Number.isFinite)) {
      points.push(fallback);
    }
  }
  return points;
}

function parseMolLikeCoordinates(content: string): Coordinate[] {
  const points: Coordinate[] = [];
  for (const line of content.split(/\r?\n/)) {
    const match = line
      .trim()
      .match(/^(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+[A-Za-z*]{1,3}\b/);
    if (!match) {
      continue;
    }
    const point = {
      x: Number.parseFloat(match[1]),
      y: Number.parseFloat(match[2]),
      z: Number.parseFloat(match[3]),
    };
    if ([point.x, point.y, point.z].every(Number.isFinite)) {
      points.push(point);
    }
  }
  return points;
}

function tokenizeCifLine(line: string): string[] {
  return line.match(/'[^']*'|"[^"]*"|\S+/g)?.map((token) => token.replace(/^['"]|['"]$/g, "")) ?? [];
}

function parseCifCoordinates(content: string): Coordinate[] {
  const lines = content.split(/\r?\n/);
  const points: Coordinate[] = [];
  let headers: string[] = [];
  let readingAtomLoop = false;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      readingAtomLoop = false;
      headers = [];
      continue;
    }
    if (line === "loop_") {
      readingAtomLoop = false;
      headers = [];
      continue;
    }
    if (line.startsWith("_atom_site.")) {
      headers.push(line.split(/\s+/)[0]);
      readingAtomLoop = true;
      continue;
    }
    if (!readingAtomLoop || !headers.length || line.startsWith("_")) {
      continue;
    }

    const xIndex = headers.indexOf("_atom_site.Cartn_x");
    const yIndex = headers.indexOf("_atom_site.Cartn_y");
    const zIndex = headers.indexOf("_atom_site.Cartn_z");
    if (xIndex < 0 || yIndex < 0 || zIndex < 0) {
      continue;
    }
    const fields = tokenizeCifLine(line);
    const point = {
      x: Number.parseFloat(fields[xIndex]),
      y: Number.parseFloat(fields[yIndex]),
      z: Number.parseFloat(fields[zIndex]),
    };
    if ([point.x, point.y, point.z].every(Number.isFinite)) {
      points.push(point);
    }
  }

  return points;
}

function boundsForStructure(structure: ViewerStructureResult): CoordinateBounds | null {
  if (!structure.ok || !structure.content) {
    return null;
  }
  const format = structure.format.toLowerCase();
  if (format === "pdb" || format === "pdbqt") {
    return coordinateBounds(parsePdbLikeCoordinates(structure.content));
  }
  if (format === "sdf" || format === "mol" || format === "mol2") {
    return coordinateBounds(parseMolLikeCoordinates(structure.content));
  }
  if (format === "cif") {
    return coordinateBounds(parseCifCoordinates(structure.content));
  }
  return null;
}

function setModelStyle(model: unknown, layerKey: ViewerLayerKey): void {
  const target = model as {
    setStyle: (selection: Record<string, unknown>, style: Record<string, unknown>) => void;
  };
  if (layerKey === "receptor_raw" || layerKey === "receptor_prepared") {
    target.setStyle({}, { cartoon: { color: "spectrum", opacity: 0.72 }, stick: { radius: 0.08 } });
    return;
  }
  if (layerKey === "pose" || layerKey === "docking_output") {
    target.setStyle({}, { stick: { radius: 0.26, colorscheme: "magentaCarbon" } });
    return;
  }
  target.setStyle({}, { stick: { radius: 0.24, colorscheme: "greenCarbon" } });
}

function addSearchBoxOverlay(viewer: ReturnType<typeof $3Dmol.createViewer>, visualization: BoxVisualizationPayload): void {
  const anyViewer = viewer as unknown as {
    addBox: (spec: Record<string, unknown>) => void;
    addCylinder?: (spec: Record<string, unknown>) => void;
    addSphere?: (spec: Record<string, unknown>) => void;
  };
  const payload = visualization.viewer_box_payload;
  anyViewer.addBox({
    ...payload,
    color: "cyan",
    alpha: 0.1,
    wireframe: false,
  });

  const corners = visualization.corners;
  const edges = [
    [0, 1],
    [0, 2],
    [0, 4],
    [3, 1],
    [3, 2],
    [3, 7],
    [5, 1],
    [5, 4],
    [5, 7],
    [6, 2],
    [6, 4],
    [6, 7],
  ];
  for (const [startIndex, endIndex] of edges) {
    anyViewer.addCylinder?.({
      start: corners[startIndex],
      end: corners[endIndex],
      radius: 0.08,
      color: "cyan",
      fromCap: 1,
      toCap: 1,
    });
  }
  anyViewer.addSphere?.({
    center: payload.center,
    radius: 0.32,
    color: "blue",
    alpha: 0.85,
  });
}

export default function ViewerPage({ project, onBack, onProjectChange }: ViewerPageProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ReturnType<typeof $3Dmol.createViewer> | null>(null);
  const [fileKind, setFileKind] = useState<ViewerFileKind>("receptor_prepared");
  const [box, setBox] = useState(project.box);
  const [boxVisualization, setBoxVisualization] = useState<BoxVisualizationPayload | null>(() =>
    buildLocalBoxVisualization(project.box),
  );
  const [showBox, setShowBox] = useState(true);
  const [boxWarnings, setBoxWarnings] = useState<string[]>([]);
  const [runId, setRunId] = useState(() => latestRunId(project));
  const [poseList, setPoseList] = useState<DockingPoseListResponse | null>(null);
  const [selectedPose, setSelectedPose] = useState<DockingPoseSummary | null>(null);
  const [status, setStatus] = useState<ViewerFileStatusResponse | null>(null);
  const [loadedLayers, setLoadedLayers] = useState<Partial<Record<ViewerLayerKey, LoadedViewerLayer>>>({});
  const [structure, setStructure] = useState<ViewerStructureResult | null>(null);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  const clearViewer = useCallback(() => {
    viewerRef.current?.clear();
    viewerRef.current = null;
    if (containerRef.current) {
      containerRef.current.innerHTML = "";
    }
  }, []);

  const renderScene = useCallback(
    (options: { fit?: boolean } = {}) => {
      const previousView = options.fit ? null : (viewerRef.current as unknown as { getView?: () => unknown })?.getView?.();
      clearViewer();
      if (!containerRef.current) {
        return;
      }

      const visibleLayers = layerOrder
        .map((key) => loadedLayers[key])
        .filter((layer): layer is LoadedViewerLayer => Boolean(layer?.visible && layer.structure.ok));
      const shouldDrawBox = Boolean(showBox && boxVisualization);
      if (!visibleLayers.length && !shouldDrawBox) {
        return;
      }

      try {
        const viewer = $3Dmol.createViewer(containerRef.current, { backgroundColor: "white" });
        for (const layer of visibleLayers) {
          if (!viewerFormats.has(layer.structure.format)) {
            continue;
          }
          const model = viewer.addModel(layer.structure.content, layer.structure.format);
          setModelStyle(model, layer.key);
        }
        if (shouldDrawBox && boxVisualization) {
          addSearchBoxOverlay(viewer, boxVisualization);
        }
        if (previousView) {
          (viewer as unknown as { setView?: (view: unknown) => void }).setView?.(previousView);
        } else {
          viewer.zoomTo();
        }
        viewer.render();
        viewerRef.current = viewer;
      } catch (error) {
        setMessage("3Dmol.js 未能显示当前视图。结构格式或内容可能不被当前 viewer 支持。");
        setRawError(error instanceof Error ? error.message : String(error));
      }
    },
    [boxVisualization, clearViewer, loadedLayers, showBox],
  );

  const setBoxAndVisualization = useCallback((next: DockStartProject["box"]) => {
    setBox(next);
    setBoxVisualization(buildLocalBoxVisualization(next));
    setBoxWarnings(localBoxWarnings(next));
  }, []);

  const readStructure = useCallback(
    async (kind: ViewerFileKind): Promise<ViewerStructureResult> => {
      const rawPayload = await invoke<string>("load_structure_for_viewer", {
        projectDir: project.project_dir,
        fileKind: kind,
      });
      return parseViewerStructure(rawPayload);
    },
    [project.project_dir],
  );

  const addStructureLayer = useCallback((kind: ViewerLayerKey, parsed: ViewerStructureResult) => {
    setLoadedLayers((previous) => ({
      ...previous,
      [kind]: {
        key: kind,
        label: layerLabels[kind],
        structure: parsed,
        visible: true,
      },
    }));
  }, []);

  const reloadStatus = useCallback(async () => {
    setIsBusy(true);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("get_viewer_file_status", {
        projectDir: project.project_dir,
      });
      const parsed = parseViewerStatus(rawPayload);
      setStatus(parsed);
      setMessage(parsed.message ?? "Viewer 文件状态已读取。");
      if (!parsed.ok) {
        setRawError(parsed.error?.raw_error ?? "");
      }
    } catch (error) {
      setMessage("前端未能调用 viewer 文件状态命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [project.project_dir]);

  const loadStructure = useCallback(async () => {
    setIsBusy(true);
    setRawError("");
    try {
      const parsed = await readStructure(fileKind);
      setStructure(parsed);
      setSelectedPose(null);
      if (!parsed.ok) {
        setMessage(parsed.error?.message ?? parsed.message ?? "结构文件无法加载。");
        setRawError(parsed.error?.raw_error ?? "");
        return;
      }
      addStructureLayer(fileKind, parsed);
      setMessage(`${layerLabels[fileKind]} 已加入 3D 视图。`);
    } catch (error) {
      setMessage("前端未能调用结构读取命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [addStructureLayer, fileKind, readStructure]);

  const pickAvailableKind = useCallback(
    (prepared: ViewerFileKind, raw: ViewerFileKind): ViewerFileKind => {
      if (status?.files?.[prepared]?.ok) {
        return prepared;
      }
      if (status?.files?.[raw]?.ok) {
        return raw;
      }
      return prepared;
    },
    [status?.files],
  );

  const loadLayerSet = useCallback(
    async (kinds: ViewerFileKind[]) => {
      setIsBusy(true);
      setRawError("");
      const nextLayers: Partial<Record<ViewerLayerKey, LoadedViewerLayer>> = {};
      const failures: string[] = [];
      try {
        for (const kind of kinds) {
          const parsed = await readStructure(kind);
          if (parsed.ok) {
            nextLayers[kind] = {
              key: kind,
              label: layerLabels[kind],
              structure: parsed,
              visible: true,
            };
            setStructure(parsed);
          } else {
            failures.push(`${layerLabels[kind]}：${parsed.error?.message ?? parsed.message}`);
          }
        }
        setLoadedLayers((previous) => ({ ...previous, ...nextLayers }));
        const loadedCount = Object.keys(nextLayers).length;
        setMessage(
          loadedCount
            ? `已加载 ${loadedCount} 个结构图层。${failures.length ? `未加载：${failures.join("；")}` : ""}`
            : failures.join("；") || "没有可加载的结构图层。",
        );
      } catch (error) {
        setMessage("前端未能批量加载结构。");
        setRawError(error instanceof Error ? error.message : String(error));
      } finally {
        setIsBusy(false);
      }
    },
    [readStructure],
  );

  const loadDefaultPair = useCallback(async () => {
    const receptorKind = pickAvailableKind("receptor_prepared", "receptor_raw");
    const ligandKind = pickAvailableKind("ligand_prepared", "ligand_raw");
    await loadLayerSet([receptorKind, ligandKind]);
  }, [loadLayerSet, pickAvailableKind]);

  const loadAllAvailable = useCallback(async () => {
    const availableKinds = fileKindOptions
      .map((option) => option.value)
      .filter((kind) => status?.files?.[kind]?.ok);
    if (!availableKinds.length) {
      setMessage("当前没有可读取的结构文件。请先完成下载、准备或运行步骤。");
      return;
    }
    await loadLayerSet(availableKinds);
  }, [loadLayerSet, status?.files]);

  const loadPoseList = useCallback(async () => {
    if (!runId.trim()) {
      setMessage("请先输入运行记录，例如 run_001。");
      return;
    }
    setIsBusy(true);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("list_docking_poses", {
        projectDir: project.project_dir,
        runId: runId.trim(),
      });
      const parsed = parseDockingPoseList(rawPayload);
      setPoseList(parsed);
      if (!parsed.ok) {
        setMessage(parsed.error?.message ?? "构象列表读取失败。");
        setRawError(parsed.error?.raw_error ?? "");
        return;
      }
      setMessage(parsed.message ?? "构象列表已读取。");
    } catch (error) {
      setMessage("前端未能调用构象列表命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [project.project_dir, runId]);

  const loadPose = useCallback(
    async (mode: number) => {
      setIsBusy(true);
      setRawError("");
      try {
        const rawPayload = await invoke<string>("load_docking_pose_for_viewer", {
          projectDir: project.project_dir,
          runId: runId.trim(),
          mode,
        });
        const parsed = parseViewerStructure(rawPayload);
        if (!parsed.ok) {
          setMessage(parsed.error?.message ?? "对接构象读取失败。");
          setRawError(parsed.error?.raw_error ?? "");
          return;
        }

        let receptorLayer: LoadedViewerLayer | null = null;
        if (!loadedLayers.receptor_prepared) {
          try {
            const receptor = await readStructure("receptor_prepared");
            if (receptor.ok) {
              receptorLayer = {
                key: "receptor_prepared",
                label: layerLabels.receptor_prepared,
                structure: receptor,
                visible: true,
              };
            }
          } catch {
            receptorLayer = null;
          }
        }

        setStructure(parsed);
        setSelectedPose(poseList?.poses.find((pose) => pose.mode === mode) ?? null);
        setFileKind("docking_output");
        setLoadedLayers((previous) => ({
          ...previous,
          ...(receptorLayer ? { receptor_prepared: receptorLayer } : {}),
          pose: {
            key: "pose",
            label: layerLabels.pose,
            structure: parsed,
            visible: true,
          },
        }));
        setMessage("已加载对接构象。评分只表示当前参数下的 docking score。");
      } catch (error) {
        setMessage("前端未能调用对接构象读取命令。");
        setRawError(error instanceof Error ? error.message : String(error));
      } finally {
        setIsBusy(false);
      }
    },
    [loadedLayers.receptor_prepared, poseList?.poses, project.project_dir, readStructure, runId],
  );

  const loadBoxVisualization = useCallback(async () => {
    try {
      const rawPayload = await invoke<string>("get_box_visualization", {
        projectDir: project.project_dir,
      });
      const parsed = parseBoxVisualization(rawPayload);
      if (!parsed.ok) {
        setMessage(parsed.error?.message ?? "Box 可视化数据读取失败。");
        setRawError(parsed.error?.raw_error ?? "");
        return;
      }
      setBox(parsed.box);
      setBoxVisualization(parsed.visualization);
      setBoxWarnings(parsed.warnings ?? []);
    } catch (error) {
      setMessage("前端未能调用 Box 可视化命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    }
  }, [project.project_dir]);

  const saveBox = useCallback(async () => {
    setIsBusy(true);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("update_box_from_visualization", {
        projectDir: project.project_dir,
        boxJson: JSON.stringify(box),
      });
      const parsed = parseBoxVisualization(rawPayload);
      if (!parsed.ok) {
        setMessage(parsed.error?.message ?? "Box 参数保存失败。");
        setRawError(parsed.error?.raw_error ?? "");
        return;
      }
      setBox(parsed.box);
      setBoxVisualization(parsed.visualization);
      setBoxWarnings(parsed.warnings ?? []);
      if (parsed.project) {
        onProjectChange(parsed.project);
      }
      setMessage(parsed.message ?? "Box 参数已保存。");
    } catch (error) {
      setMessage("前端未能调用 Box 保存命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [box, onProjectChange, project.project_dir]);

  const updateBoxField = (key: keyof DockStartProject["box"], value: number) => {
    setBoxAndVisualization({ ...box, [key]: value });
  };

  const toggleLayer = (key: ViewerLayerKey) => {
    setLoadedLayers((previous) => {
      const layer = previous[key];
      if (!layer) {
        return previous;
      }
      return {
        ...previous,
        [key]: { ...layer, visible: !layer.visible },
      };
    });
  };

  const removeLayer = (key: ViewerLayerKey) => {
    setLoadedLayers((previous) => {
      const next = { ...previous };
      delete next[key];
      return next;
    });
  };

  const clearScene = () => {
    setLoadedLayers({});
    setSelectedPose(null);
    setStructure(null);
    clearViewer();
    setMessage("视图已清空，Box 参数仍保留在当前项目中。");
  };

  const locateBoxFromLayer = (keys: ViewerLayerKey[], mode: "center" | "fit") => {
    for (const key of keys) {
      const layer = loadedLayers[key];
      const bounds = layer ? boundsForStructure(layer.structure) : null;
      if (!layer || !bounds) {
        continue;
      }
      const next: DockStartProject["box"] = {
        ...box,
        center_x: roundBoxValue(bounds.center.x),
        center_y: roundBoxValue(bounds.center.y),
        center_z: roundBoxValue(bounds.center.z),
        ...(mode === "fit"
          ? {
              size_x: roundBoxValue(Math.max(minBoxDimension, bounds.size.x + fitMarginAngstrom)),
              size_y: roundBoxValue(Math.max(minBoxDimension, bounds.size.y + fitMarginAngstrom)),
              size_z: roundBoxValue(Math.max(minBoxDimension, bounds.size.z + fitMarginAngstrom)),
            }
          : {}),
      };
      setBoxAndVisualization(next);
      setShowBox(true);
      setMessage(
        mode === "fit"
          ? `搜索范围已按 ${layer.label} 的几何范围外扩 ${fitMarginAngstrom} Å。`
          : `搜索范围中心已移动到 ${layer.label} 的几何中心。`,
      );
      return;
    }
    setMessage("请先加载配体或对接构象；当前可视对象没有可解析的三维坐标。");
  };

  useEffect(() => {
    void reloadStatus();
    void loadBoxVisualization();
    return () => {
      clearViewer();
    };
  }, [clearViewer, loadBoxVisualization, reloadStatus]);

  useEffect(() => {
    renderScene();
  }, [renderScene]);

  const selectedStatus = status?.files?.[fileKind];
  const selectedOption = fileKindOptions.find((item) => item.value === fileKind);
  const loadedLayerList = layerOrder.map((key) => loadedLayers[key]).filter(Boolean) as LoadedViewerLayer[];
  const visibleLayerList = loadedLayerList.filter((layer) => layer.visible);
  const visibleLayerSummary =
    visibleLayerList.length > 0 ? visibleLayerList.map((layer) => layer.label).join(" + ") : "仅显示搜索范围";

  const currentBounds = structure ? boundsForStructure(structure) : null;
  const ligandTargetAvailable = Boolean(loadedLayers.ligand_prepared || loadedLayers.ligand_raw);
  const poseTargetAvailable = Boolean(loadedLayers.pose);

  const boxSummary = boxVisualization ?? buildLocalBoxVisualization(box);

  return (
    <section className="project-page viewer-page">
      <PageHeader
        eyebrow="3D 工作台"
        title="3D 分子工作台"
        description="叠加结构、构象和搜索范围，用可见 Box 设置 Vina 空间。"
        actions={
          <button className="text-button" type="button" onClick={onBack}>
            返回上一页
          </button>
        }
      />
      <section className="viewer-workspace-grid" aria-label="3D viewer workspace">
        <aside className="viewer-control-column" aria-label="结构 Inspector">
          <SectionCard title="结构来源" description={selectedOption?.description}>
            <label className="viewer-source-row">
              <span>结构来源</span>
              <select value={fileKind} onChange={(event) => setFileKind(event.target.value as ViewerFileKind)}>
                {fileKindOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="viewer-quick-actions">
              <button className="primary-button" type="button" disabled={isBusy} onClick={() => void loadStructure()}>
                加载到视图
              </button>
              <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void loadDefaultPair()}>
                加载受体+配体
              </button>
              <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void loadAllAvailable()}>
                加载全部可用
              </button>
              <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void reloadStatus()}>
                重新读取状态
              </button>
            </div>
          </SectionCard>

          <SectionCard title="图层" description="多个结构可以同时显示。">
            <div className="viewer-layer-list">
              {layerOrder.map((key) => {
                const layer = loadedLayers[key];
                return (
                  <div key={key} className={`viewer-layer-row ${layer?.visible ? "visible" : ""}`}>
                    <label>
                      <input
                        type="checkbox"
                        checked={Boolean(layer?.visible)}
                        disabled={!layer}
                        onChange={() => toggleLayer(key)}
                      />
                      <span>{layerLabels[key]}</span>
                    </label>
                    <div className="viewer-layer-actions">
                      <StatusBadge tone={layer ? (layer.visible ? "ok" : "muted") : "muted"}>
                        {layer ? (layer.visible ? "显示" : "隐藏") : "未加载"}
                      </StatusBadge>
                      {layer ? (
                        <button className="text-button inline" type="button" onClick={() => removeLayer(key)}>
                          移除
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </SectionCard>

          <SectionCard title="对接构象" description="读取 run 输出中的 mode。">
            <label className="viewer-source-row">
              <span>运行记录</span>
              <input
                type="text"
                value={runId}
                placeholder="run_001"
                onChange={(event) => setRunId(event.target.value)}
              />
            </label>
            <div className="toolbar project-toolbar">
              <button
                className="secondary-button"
                type="button"
                disabled={isBusy || !runId.trim()}
                onClick={() => void loadPoseList()}
              >
                读取构象列表
              </button>
            </div>
            {poseList?.warnings?.length ? (
              <WarningCallout title="构象读取提示">
                {poseList.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </WarningCallout>
            ) : null}
          </SectionCard>

          <SectionCard title="搜索范围" description="单位：Å。Box 会随输入实时更新。">
            <label className="viewer-toggle-row">
              <input type="checkbox" checked={showBox} onChange={(event) => setShowBox(event.target.checked)} />
              <span>显示搜索范围</span>
            </label>
            <div className="box-control-grid">
              {(["center_x", "center_y", "center_z", "size_x", "size_y", "size_z"] as Array<
                keyof DockStartProject["box"]
              >).map((key) => (
                <label key={key} className="box-control">
                  <span>{boxFieldLabel(key)}</span>
                  <input
                    type="number"
                    step="0.1"
                    value={box[key]}
                    onChange={(event) => updateBoxField(key, Number(event.target.value))}
                  />
                </label>
              ))}
            </div>
            <div className="viewer-box-tools">
              <button
                className="secondary-button"
                type="button"
                disabled={!ligandTargetAvailable}
                onClick={() => locateBoxFromLayer(["ligand_prepared", "ligand_raw"], "center")}
              >
                以配体居中
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={!ligandTargetAvailable}
                onClick={() => locateBoxFromLayer(["ligand_prepared", "ligand_raw"], "fit")}
              >
                围住配体
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={!poseTargetAvailable}
                onClick={() => locateBoxFromLayer(["pose"], "center")}
              >
                以构象居中
              </button>
            </div>
            <p className="viewer-geometry-note">这些按钮只按已加载对象的几何坐标定位，不做结合位点预测。</p>
            <div className="toolbar project-toolbar">
              <button className="primary-button" type="button" disabled={isBusy} onClick={() => void saveBox()}>
                保存搜索范围
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={isBusy}
                onClick={() => void loadBoxVisualization()}
              >
                重新读取范围
              </button>
            </div>
            {(boxWarnings.length ? boxWarnings : localBoxWarnings(box)).map((warning) => (
              <WarningCallout key={warning} title="搜索范围提示">
                <p>{warning}</p>
              </WarningCallout>
            ))}
          </SectionCard>
        </aside>

        <section className="viewer-stage" aria-label="3D molecular viewer">
          <div className="viewer-stage-toolbar">
            <div className="viewer-stage-title">
              <span>当前视图</span>
              <strong>{visibleLayerSummary}</strong>
            </div>
            <div className="viewer-stage-actions">
              <StatusBadge tone={showBox && boxVisualization ? "info" : "muted"}>
                {showBox && boxVisualization ? "Box 已显示" : "Box 已隐藏"}
              </StatusBadge>
              <button className="text-button inline" type="button" onClick={() => renderScene({ fit: true })}>
                Zoom to fit
              </button>
              <button className="text-button inline" type="button" onClick={clearScene}>
                清空视图
              </button>
            </div>
          </div>
          <div className="viewer-canvas" ref={containerRef} aria-label="3D molecular viewer canvas" />
        </section>

        <aside className="viewer-inspector-column" aria-label="Properties">
          <SectionCard title="当前文件">
            <dl className="tool-meta">
              <div>
                <dt>当前选择</dt>
                <dd>{selectedOption?.label}</dd>
              </div>
              <div>
                <dt>状态</dt>
                <dd>
                  <StatusBadge tone={viewerStatusTone(selectedStatus)}>{displayStatus(selectedStatus)}</StatusBadge>
                </dd>
              </div>
              <div>
                <dt>路径</dt>
                <dd>
                  <code>{structure?.relative_path || selectedStatus?.relative_path || "未记录"}</code>
                </dd>
              </div>
              <div>
                <dt>格式</dt>
                <dd>{structure?.format || selectedStatus?.format || "unknown"}</dd>
              </div>
              <div>
                <dt>大小</dt>
                <dd>{formatBytes(structure?.size_bytes ?? selectedStatus?.size_bytes ?? 0)}</dd>
              </div>
              <div>
                <dt>坐标</dt>
                <dd>{currentBounds ? `${currentBounds.atomCount} 个原子坐标` : "未解析"}</dd>
              </div>
            </dl>
            {structure?.warnings?.length ? (
              <WarningCallout title="结构文件提示">
                {structure.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </WarningCallout>
            ) : null}
          </SectionCard>

          <SectionCard title="Box 摘要">
            <dl className="tool-meta">
              <div>
                <dt>中心</dt>
                <dd>
                  {box.center_x}, {box.center_y}, {box.center_z} Å
                </dd>
              </div>
              <div>
                <dt>尺寸</dt>
                <dd>
                  {box.size_x} × {box.size_y} × {box.size_z} Å
                </dd>
              </div>
              <div>
                <dt>最小坐标</dt>
                <dd>
                  {boxSummary
                    ? `${roundBoxValue(boxSummary.min.x)}, ${roundBoxValue(boxSummary.min.y)}, ${roundBoxValue(
                        boxSummary.min.z,
                      )}`
                    : "不可用"}
                </dd>
              </div>
              <div>
                <dt>最大坐标</dt>
                <dd>
                  {boxSummary
                    ? `${roundBoxValue(boxSummary.max.x)}, ${roundBoxValue(boxSummary.max.y)}, ${roundBoxValue(
                        boxSummary.max.z,
                      )}`
                    : "不可用"}
                </dd>
              </div>
            </dl>
          </SectionCard>

          <SectionCard title="可查看文件">
            <div className="viewer-file-status-list">
              {fileKindOptions.map((option) => {
                const item = status?.files?.[option.value];
                return (
                  <div key={option.value} className="viewer-file-status-row">
                    <div>
                      <strong>{option.label}</strong>
                      <span>{item?.relative_path || "未记录路径"}</span>
                    </div>
                    <StatusBadge tone={viewerStatusTone(item)}>{displayStatus(item)}</StatusBadge>
                  </div>
                );
              })}
            </div>
          </SectionCard>

          <SectionCard title="Pose 列表">
            {poseList?.poses?.length ? (
              <div className="scores-table-wrap compact-score-table">
                <table className="scores-table">
                  <thead>
                    <tr>
                      <th>构象</th>
                      <th>对接评分</th>
                      <th>RMSD l.b.</th>
                      <th>RMSD u.b.</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {poseList.poses.map((pose) => (
                      <tr key={pose.mode}>
                        <td>{pose.mode}</td>
                        <td>{pose.affinity_kcal_mol ?? "未解析"}</td>
                        <td>{pose.rmsd_lb ?? "未解析"}</td>
                        <td>{pose.rmsd_ub ?? "未解析"}</td>
                        <td>
                          <button
                            className="text-button inline"
                            type="button"
                            disabled={isBusy}
                            onClick={() => void loadPose(pose.mode)}
                          >
                            查看
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="placeholder-note">尚未读取构象列表。</p>
            )}
            <dl className="tool-meta">
              <div>
                <dt>当前构象</dt>
                <dd>
                  {selectedPose
                    ? `mode ${selectedPose.mode}, 对接评分 ${selectedPose.affinity_kcal_mol ?? "未解析"}`
                    : "未选择"}
                </dd>
              </div>
            </dl>
          </SectionCard>

          <CommandResultPanel title="Viewer 状态" message={message} rawError={rawError} />
        </aside>
      </section>
      <WarningCallout title="Viewer 边界">
        <p>3D 工作台只做几何查看和搜索范围复核，不做相互作用分析、pocket prediction 或药效判断。</p>
      </WarningCallout>
    </section>
  );
}
