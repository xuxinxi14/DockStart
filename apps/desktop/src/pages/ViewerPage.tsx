import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
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
import { addOrientationAxes } from "../components/viewerSceneHelpers";

type ViewerPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  initialRunId?: string;
  initialMode?: number | null;
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

const boxFieldKeys = ["center_x", "center_y", "center_z", "size_x", "size_y", "size_z"] as const;
type BoxFieldKey = (typeof boxFieldKeys)[number];
type BoxInputValues = Record<BoxFieldKey, string>;
type BoxInputErrors = Partial<Record<BoxFieldKey, string>>;

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
const minEditableBoxDimension = 0.1;
const wheelStepThreshold = 72;
const boxStepOptions = [
  { label: "细调", value: 0.1 },
  { label: "常规", value: 1 },
  { label: "快速", value: 5 },
];

function boxToInputValues(box: DockStartProject["box"]): BoxInputValues {
  return boxFieldKeys.reduce(
    (values, key) => ({ ...values, [key]: String(box[key]) }),
    {} as BoxInputValues,
  );
}

function parseBoxInput(key: BoxFieldKey, rawValue: string): { value: number | null; error: string } {
  const valueText = rawValue.trim();
  if (!valueText) {
    return { value: null, error: "请输入数值" };
  }
  const value = Number(valueText);
  if (!Number.isFinite(value)) {
    return { value: null, error: "数值格式无效" };
  }
  if (key.startsWith("size_") && value < minEditableBoxDimension) {
    return { value: null, error: `尺寸不得小于 ${minEditableBoxDimension} Å` };
  }
  return { value, error: "" };
}

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
  if (!result.relative_path) {
    return "未记录";
  }
  if (!result.exists) {
    return "缺失";
  }
  if (result.error) {
    return "需处理";
  }
  return "不可读取";
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

export default function ViewerPage({ project, onBack, onProjectChange, initialRunId = "", initialMode = null }: ViewerPageProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasShellRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ReturnType<typeof $3Dmol.createViewer> | null>(null);
  const [fileKind, setFileKind] = useState<ViewerFileKind>("receptor_prepared");
  const [box, setBox] = useState(project.box);
  const [boxInputs, setBoxInputs] = useState<BoxInputValues>(() => boxToInputValues(project.box));
  const [boxInputErrors, setBoxInputErrors] = useState<BoxInputErrors>({});
  const [boxVisualization, setBoxVisualization] = useState<BoxVisualizationPayload | null>(() =>
    buildLocalBoxVisualization(project.box),
  );
  const [showBox, setShowBox] = useState(true);
  const boxVisualizationRef = useRef<BoxVisualizationPayload | null>(boxVisualization);
  const showBoxRef = useRef(showBox);
  const [boxStep, setBoxStep] = useState(0.1);
  const boxStepRef = useRef(boxStep);
  const boxRef = useRef(box);
  const boxInputsRef = useRef(boxInputs);
  const savedBoxRef = useRef(project.box);
  const [boundBoxField, setBoundBoxField] = useState<BoxFieldKey | null>(null);
  const boundBoxFieldRef = useRef<BoxFieldKey | null>(null);
  const [boxWarnings, setBoxWarnings] = useState<string[]>([]);
  const [runId, setRunId] = useState(() => initialRunId || latestRunId(project));
  const [poseList, setPoseList] = useState<DockingPoseListResponse | null>(null);
  const [selectedPose, setSelectedPose] = useState<DockingPoseSummary | null>(null);
  const [status, setStatus] = useState<ViewerFileStatusResponse | null>(null);
  const [loadedLayers, setLoadedLayers] = useState<Partial<Record<ViewerLayerKey, LoadedViewerLayer>>>({});
  const [structure, setStructure] = useState<ViewerStructureResult | null>(null);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const initialPoseRequestHandled = useRef(false);

  const destroyViewer = useCallback(() => {
    viewerRef.current?.clear();
    viewerRef.current = null;
    if (containerRef.current) {
      containerRef.current.replaceChildren();
    }
  }, []);

  const ensureViewer = useCallback(() => {
    if (viewerRef.current) {
      return viewerRef.current;
    }
    if (!containerRef.current) {
      return null;
    }
    const viewerBackgroundColor = getComputedStyle(document.documentElement)
      .getPropertyValue("--ds-viewer-bg")
      .trim();
    const viewer = $3Dmol.createViewer(containerRef.current, { backgroundColor: viewerBackgroundColor });
    viewerRef.current = viewer;
    return viewer;
  }, []);

  const renderScene = useCallback(
    (options: { fit?: boolean } = {}) => {
      const previousView = options.fit ? null : (viewerRef.current as unknown as { getView?: () => unknown })?.getView?.();
      const visibleLayers = layerOrder
        .map((key) => loadedLayers[key])
        .filter((layer): layer is LoadedViewerLayer => Boolean(layer?.visible && layer.structure.ok));
      const viewer = ensureViewer();
      if (!viewer) {
        return;
      }

      try {
        viewer.clear();
        for (const layer of visibleLayers) {
          if (!viewerFormats.has(layer.structure.format)) {
            continue;
          }
          const model = viewer.addModel(layer.structure.content, layer.structure.format);
          setModelStyle(model, layer.key);
        }
        if (showBoxRef.current && boxVisualizationRef.current) {
          addSearchBoxOverlay(viewer, boxVisualizationRef.current);
        }
        addOrientationAxes(viewer, boxVisualizationRef.current);
        if (previousView) {
          (viewer as unknown as { setView?: (view: unknown) => void }).setView?.(previousView);
        } else {
          viewer.zoomTo();
        }
        viewer.render();
      } catch (error) {
        setMessage("3Dmol.js 未能显示当前视图。结构格式或内容可能不被当前 viewer 支持。");
        setRawError(error instanceof Error ? error.message : String(error));
      }
    },
    [ensureViewer, loadedLayers],
  );

  const refreshBoxOverlay = useCallback(() => {
    const viewer = ensureViewer();
    if (!viewer) {
      return;
    }
    try {
      const overlayViewer = viewer as unknown as {
        removeAllShapes?: () => void;
        removeAllLabels?: () => void;
      };
      overlayViewer.removeAllShapes?.();
      overlayViewer.removeAllLabels?.();
      if (showBoxRef.current && boxVisualizationRef.current) {
        addSearchBoxOverlay(viewer, boxVisualizationRef.current);
      }
      addOrientationAxes(viewer, boxVisualizationRef.current);
      viewer.render();
    } catch (error) {
      setMessage("搜索范围增量刷新失败，结构视图仍保留。可尝试重新进入 3D 工作台。");
      setRawError(error instanceof Error ? error.message : String(error));
    }
  }, [ensureViewer]);

  const fitScene = useCallback(() => {
    const viewer = viewerRef.current;
    if (!viewer) {
      return;
    }
    viewer.zoomTo();
    viewer.render();
  }, []);

  const applyBoxPreview = useCallback((next: DockStartProject["box"]) => {
    boxRef.current = next;
    setBox(next);
    setBoxVisualization(buildLocalBoxVisualization(next));
    setBoxWarnings(localBoxWarnings(next));
  }, []);

  const syncBoxState = useCallback(
    (next: DockStartProject["box"]) => {
      applyBoxPreview(next);
      const nextInputs = boxToInputValues(next);
      boxInputsRef.current = nextInputs;
      setBoxInputs(nextInputs);
      setBoxInputErrors({});
    },
    [applyBoxPreview],
  );

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

  const loadPoseList = useCallback(async (requestedRunId = runId.trim()): Promise<DockingPoseListResponse | null> => {
    const targetRunId = requestedRunId.trim();
    if (!targetRunId) {
      setMessage("请先输入运行记录，例如 run_001。");
      return null;
    }
    setIsBusy(true);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("list_docking_poses", {
        projectDir: project.project_dir,
        runId: targetRunId,
      });
      const parsed = parseDockingPoseList(rawPayload);
      setPoseList(parsed);
      if (!parsed.ok) {
        setMessage(parsed.error?.message ?? "构象列表读取失败。");
        setRawError(parsed.error?.raw_error ?? "");
        return parsed;
      }
      setMessage(parsed.message ?? "构象列表已读取。");
      return parsed;
    } catch (error) {
      setMessage("前端未能调用构象列表命令。");
      setRawError(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setIsBusy(false);
    }
  }, [project.project_dir, runId]);

  const loadPose = useCallback(
    async (mode: number, requestedRunId = runId.trim(), requestedPoses = poseList?.poses) => {
      setIsBusy(true);
      setRawError("");
      try {
        const rawPayload = await invoke<string>("load_docking_pose_for_viewer", {
          projectDir: project.project_dir,
          runId: requestedRunId.trim(),
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
        setSelectedPose(requestedPoses?.find((pose) => pose.mode === mode) ?? null);
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
      savedBoxRef.current = parsed.box;
      syncBoxState(parsed.box);
      setBoxVisualization(parsed.visualization);
      setBoxWarnings(parsed.warnings ?? []);
    } catch (error) {
      setMessage("前端未能调用 Box 可视化命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    }
  }, [project.project_dir, syncBoxState]);

  const collectBoxInputs = useCallback((): DockStartProject["box"] | null => {
    const next = { ...box };
    const errors: BoxInputErrors = {};
    for (const key of boxFieldKeys) {
      const parsed = parseBoxInput(key, boxInputs[key]);
      if (parsed.value === null) {
        errors[key] = parsed.error;
      } else {
        next[key] = parsed.value;
      }
    }
    setBoxInputErrors(errors);
    return Object.keys(errors).length ? null : next;
  }, [box, boxInputs]);

  const applyPendingBox = useCallback(() => {
    const next = collectBoxInputs();
    if (!next) {
      setMessage("部分 Box 参数尚未完成，请修正标出的输入项后再应用预览。");
      return false;
    }
    syncBoxState(next);
    setMessage("搜索范围预览已更新；如需写入 project.json，请继续保存。");
    return true;
  }, [collectBoxInputs, syncBoxState]);

  const saveBox = useCallback(async () => {
    const nextBox = collectBoxInputs();
    if (!nextBox) {
      setMessage("部分 Box 参数尚未完成，未写入 project.json。");
      setRawError("");
      return;
    }
    syncBoxState(nextBox);
    setIsBusy(true);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("update_box_from_visualization", {
        projectDir: project.project_dir,
        boxJson: JSON.stringify(nextBox),
      });
      const parsed = parseBoxVisualization(rawPayload);
      if (!parsed.ok) {
        setMessage(parsed.error?.message ?? "Box 参数保存失败。");
        setRawError(parsed.error?.raw_error ?? "");
        return;
      }
      savedBoxRef.current = parsed.box;
      syncBoxState(parsed.box);
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
  }, [collectBoxInputs, onProjectChange, project.project_dir, syncBoxState]);

  const updateBoxInput = (key: BoxFieldKey, value: string) => {
    setBoxInputs((previous) => {
      const next = { ...previous, [key]: value };
      boxInputsRef.current = next;
      return next;
    });
    setBoxInputErrors((previous) => {
      if (!previous[key]) {
        return previous;
      }
      const next = { ...previous };
      delete next[key];
      return next;
    });
  };

  const commitBoxField = (key: BoxFieldKey) => {
    const parsed = parseBoxInput(key, boxInputs[key]);
    if (parsed.value === null) {
      setBoxInputErrors((previous) => ({ ...previous, [key]: parsed.error }));
      return;
    }
    const next = { ...boxRef.current, [key]: parsed.value };
    applyBoxPreview(next);
    setBoxInputs((previous) => {
      const nextInputs = { ...previous, [key]: String(parsed.value) };
      boxInputsRef.current = nextInputs;
      return nextInputs;
    });
    setBoxInputErrors((previous) => {
      const nextErrors = { ...previous };
      delete nextErrors[key];
      return nextErrors;
    });
  };

  const stepBoxField = useCallback((key: BoxFieldKey, direction: 1 | -1) => {
    const currentInputs = boxInputsRef.current;
    const currentBox = boxRef.current;
    const parsed = parseBoxInput(key, currentInputs[key]);
    const baseValue = parsed.value ?? currentBox[key];
    let nextValue = roundBoxValue(baseValue + boxStepRef.current * direction);
    if (key.startsWith("size_") && nextValue <= 0) {
      nextValue = minEditableBoxDimension;
    }
    const nextInputs = { ...currentInputs, [key]: String(nextValue) };
    boxInputsRef.current = nextInputs;
    setBoxInputs(nextInputs);
    setBoxInputErrors((previous) => {
      if (!previous[key]) return previous;
      const nextErrors = { ...previous };
      delete nextErrors[key];
      return nextErrors;
    });
    applyBoxPreview({ ...currentBox, [key]: nextValue });
  }, [applyBoxPreview]);

  const changeBoxStep = (nextStep: number) => {
    boxStepRef.current = nextStep;
    setBoxStep(nextStep);
  };

  const toggleWheelBinding = (key: BoxFieldKey) => {
    const next = boundBoxFieldRef.current === key ? null : key;
    boundBoxFieldRef.current = next;
    setBoundBoxField(next);
    setMessage(
      next
        ? `滚轮已绑定到${boxFieldLabel(next)}；在 3D 视图中滚动可按当前步进调整。`
        : "滚轮参数绑定已取消；在 3D 视图中滚动将恢复整体缩放。",
    );
  };

  const handleBoxInputKeyDown = (key: BoxFieldKey, event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitBoxField(key);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      updateBoxInput(key, String(box[key]));
      return;
    }
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      stepBoxField(key, event.key === "ArrowUp" ? 1 : -1);
    }
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
      syncBoxState(next);
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
      destroyViewer();
    };
  }, [destroyViewer, loadBoxVisualization, reloadStatus]);

  useEffect(() => {
    if (!initialRunId || initialPoseRequestHandled.current) return;
    initialPoseRequestHandled.current = true;
    setRunId(initialRunId);
    void (async () => {
      const list = await loadPoseList(initialRunId);
      if (initialMode !== null && list?.ok) {
        await loadPose(initialMode, initialRunId, list.poses);
      }
    })();
  }, [initialMode, initialRunId, loadPose, loadPoseList]);

  useEffect(() => {
    renderScene();
  }, [renderScene]);

  useEffect(() => {
    boxVisualizationRef.current = boxVisualization;
    showBoxRef.current = showBox;
    refreshBoxOverlay();
  }, [boxVisualization, refreshBoxOverlay, showBox]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(() => {
      const viewer = viewerRef.current as unknown as { resize?: () => void; render?: () => void } | null;
      viewer?.resize?.();
      viewer?.render?.();
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const shell = canvasShellRef.current;
    if (!shell) return;
    let accumulatedDelta = 0;

    const handleBoundWheel = (event: WheelEvent) => {
      const key = boundBoxFieldRef.current;
      if (!key) return;
      event.preventDefault();
      event.stopPropagation();

      const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE
        ? 36
        : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
          ? 120
          : 1;
      accumulatedDelta += event.deltaY * unit;
      if (Math.abs(accumulatedDelta) < wheelStepThreshold) return;

      const direction: 1 | -1 = accumulatedDelta < 0 ? 1 : -1;
      const steps = Math.min(4, Math.max(1, Math.floor(Math.abs(accumulatedDelta) / wheelStepThreshold)));
      accumulatedDelta %= wheelStepThreshold;
      for (let index = 0; index < steps; index += 1) {
        stepBoxField(key, direction);
      }
    };

    shell.addEventListener("wheel", handleBoundWheel, { capture: true, passive: false });
    return () => shell.removeEventListener("wheel", handleBoundWheel, { capture: true });
  }, [stepBoxField]);

  const selectedStatus = status?.files?.[fileKind];
  const selectedOption = fileKindOptions.find((item) => item.value === fileKind);
  const loadedLayerList = layerOrder.map((key) => loadedLayers[key]).filter(Boolean) as LoadedViewerLayer[];
  const visibleLayerList = loadedLayerList.filter((layer) => layer.visible);
  const isBoxVisible = Boolean(showBox && boxVisualization);
  const visibleLayerSummary =
    visibleLayerList.length > 0
      ? visibleLayerList.map((layer) => layer.label).join(" + ")
      : isBoxVisible
        ? "仅显示搜索范围"
        : "暂无可见图层";

  const currentBounds = structure ? boundsForStructure(structure) : null;
  const ligandTargetAvailable = Boolean(loadedLayers.ligand_prepared || loadedLayers.ligand_raw);
  const poseTargetAvailable = Boolean(loadedLayers.pose);

  const movePoseSelection = (direction: -1 | 1) => {
    const poses = poseList?.poses ?? [];
    if (!poses.length || isBusy) return;
    const currentIndex = selectedPose ? poses.findIndex((pose) => pose.mode === selectedPose.mode) : -1;
    const fallbackIndex = direction > 0 ? 0 : poses.length - 1;
    const nextIndex = currentIndex < 0
      ? fallbackIndex
      : Math.max(0, Math.min(poses.length - 1, currentIndex + direction));
    void loadPose(poses[nextIndex].mode);
  };

  const handlePoseTableKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      movePoseSelection(1);
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      movePoseSelection(-1);
    }
  };

  const boxSummary = boxVisualization ?? buildLocalBoxVisualization(box);
  const hasPendingBoxEdits = boxFieldKeys.some((key) => boxInputs[key].trim() !== String(box[key]));
  const hasUnsavedBoxChanges = boxFieldKeys.some(
    (key) => boxInputs[key].trim() !== String(savedBoxRef.current[key]),
  );
  const hasBoxInputErrors = Object.keys(boxInputErrors).length > 0;

  return (
    <section className="project-page viewer-page">
      <PageHeader
        eyebrow="3D 工作台"
        title="3D 分子工作台"
        description="在同一视图叠加受体、配体、对接构象与搜索范围；所有定位操作只使用已加载结构的几何坐标。"
        actions={
          <button className="text-button" type="button" onClick={onBack} aria-label="返回上一工作流页面">
            返回上一页
          </button>
        }
      />
      <section className="viewer-workspace-grid" aria-label="3D 分子工作台" aria-busy={isBusy}>
        <aside className="viewer-control-column" aria-label="结构与搜索范围控制">
          <SectionCard className="viewer-source-card" title="结构来源" description={selectedOption?.description}>
            <label className="viewer-source-row" htmlFor="viewer-file-kind">
              <span>结构来源</span>
              <select
                id="viewer-file-kind"
                value={fileKind}
                onChange={(event) => setFileKind(event.target.value as ViewerFileKind)}
                aria-describedby="viewer-source-help"
              >
                {fileKindOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="viewer-field-help" id="viewer-source-help">
              加载会新增或更新对应图层，不会覆盖项目文件。
            </p>
            <div className="viewer-quick-actions">
              <button
                className="primary-button"
                type="button"
                disabled={isBusy}
                onClick={() => void loadStructure()}
                aria-label={`加载${selectedOption?.label ?? "所选结构"}到 3D 视图`}
              >
                加载到视图
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={isBusy}
                onClick={() => void loadDefaultPair()}
              >
                加载受体+配体
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={isBusy}
                onClick={() => void loadAllAvailable()}
              >
                加载全部可用
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={isBusy}
                onClick={() => void reloadStatus()}
              >
                重新读取状态
              </button>
            </div>
          </SectionCard>

          <SectionCard title="图层" description="多个结构可以同时显示。">
            <div className="viewer-layer-list">
              {layerOrder.map((key) => {
                const layer = loadedLayers[key];
                return (
                  <div
                    key={key}
                    className={`viewer-layer-row ${layer?.visible ? "visible" : ""}`}
                    aria-label={`${layerLabels[key]}：${layer ? (layer.visible ? "显示" : "隐藏") : "未加载"}`}
                  >
                    <label>
                      <input
                        type="checkbox"
                        checked={Boolean(layer?.visible)}
                        disabled={!layer}
                        onChange={() => toggleLayer(key)}
                        aria-label={`${layer?.visible ? "隐藏" : "显示"}${layerLabels[key]}`}
                      />
                      <span>{layerLabels[key]}</span>
                    </label>
                    <div className="viewer-layer-actions">
                      <StatusBadge tone={layer ? (layer.visible ? "ok" : "muted") : "muted"}>
                        {layer ? (layer.visible ? "显示" : "隐藏") : "未加载"}
                      </StatusBadge>
                      {layer ? (
                        <button
                          className="text-button inline"
                          type="button"
                          onClick={() => removeLayer(key)}
                          aria-label={`从 3D 视图移除${layerLabels[key]}`}
                        >
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
            <label className="viewer-source-row" htmlFor="viewer-run-id">
              <span>运行记录</span>
              <input
                id="viewer-run-id"
                type="text"
                value={runId}
                placeholder="run_001"
                onChange={(event) => setRunId(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                aria-describedby="viewer-run-help"
              />
            </label>
            <p className="viewer-field-help" id="viewer-run-help">
              读取列表后，可在右侧选择单个构象叠加显示。
            </p>
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
            {poseList?.poses?.length ? (
              <div
                className="viewer-pose-list"
                tabIndex={0}
                onKeyDown={handlePoseTableKeyDown}
                aria-label="构象列表，使用上下方向键切换当前构象"
              >
                {poseList.poses.map((pose) => (
                  <button
                    key={pose.mode}
                    className={`viewer-pose-row ${selectedPose?.mode === pose.mode ? "is-selected" : ""}`}
                    type="button"
                    disabled={isBusy}
                    onClick={() => void loadPose(pose.mode)}
                    aria-label={`查看构象 ${pose.mode}，对接评分 ${pose.affinity_kcal_mol ?? "未解析"}`}
                    aria-pressed={selectedPose?.mode === pose.mode}
                  >
                    <strong>Mode {pose.mode}</strong>
                    <span>{pose.affinity_kcal_mol ?? "—"} kcal/mol</span>
                    <small>RMSD {pose.rmsd_lb ?? "—"}–{pose.rmsd_ub ?? "—"} Å</small>
                  </button>
                ))}
              </div>
            ) : (
              <p className="placeholder-note viewer-pose-placeholder">读取后在这里选择构象。</p>
            )}
            <div className="viewer-current-pose" aria-live="polite">
              <span>当前构象</span>
              <strong>
                {selectedPose
                  ? `Mode ${selectedPose.mode} · ${selectedPose.affinity_kcal_mol ?? "未解析"} kcal/mol`
                  : "未选择"}
              </strong>
            </div>
          </SectionCard>

          <SectionCard title="搜索范围" description="单位：Å。输入期间保留原视图，确认后再增量更新 Box。">
            <div className="viewer-box-options">
              <label className="viewer-toggle-row">
                <input
                  type="checkbox"
                  checked={showBox}
                  onChange={(event) => setShowBox(event.target.checked)}
                  aria-controls="viewer-canvas"
                />
                <span>显示搜索范围</span>
              </label>
              <div className="viewer-step-control" role="group" aria-labelledby="viewer-box-step-label">
                <span id="viewer-box-step-label">调整步进</span>
                <div>
                  {boxStepOptions.map((option) => (
                    <button
                      key={option.value}
                      className={option.value === boxStep ? "is-active" : ""}
                      type="button"
                      onClick={() => changeBoxStep(option.value)}
                      aria-pressed={option.value === boxStep}
                      aria-label={`${option.label}步进，每次 ${option.value} 埃`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <p className="viewer-box-edit-help" id="viewer-box-edit-help">
              每个参数可单独绑定滚轮；同一时间只绑定一项。输入框仍支持 Enter、Esc 和 ↑/↓。
            </p>
            <div className="viewer-wheel-binding-status" aria-live="polite">
              <StatusBadge tone={boundBoxField ? "info" : "muted"}>
                {boundBoxField ? `滚轮：${boxFieldLabel(boundBoxField)}` : "滚轮：整体缩放"}
              </StatusBadge>
              <span>{boundBoxField ? "向上增大或正移，向下减小或负移" : "点击参数旁的绑定按钮开始精调"}</span>
            </div>
            <div className="box-control-grid" role="group" aria-label="搜索范围中心与尺寸参数">
              {boxFieldKeys.map((key) => {
                const errorId = `viewer-box-${key}-error`;
                const inputId = `viewer-box-${key}`;
                const isBound = boundBoxField === key;
                return (
                <div key={key} className={`box-control ${isBound ? "is-wheel-bound" : ""}`}>
                  <label className="viewer-box-label" htmlFor={inputId}>
                    {boxFieldLabel(key)} <small>Å</small>
                  </label>
                  <div className="viewer-box-input-row">
                    <input
                      id={inputId}
                      type="text"
                      inputMode="decimal"
                      role="spinbutton"
                      value={boxInputs[key]}
                      onChange={(event) => updateBoxInput(key, event.target.value)}
                      onBlur={() => commitBoxField(key)}
                      onKeyDown={(event) => handleBoxInputKeyDown(key, event)}
                      aria-invalid={Boolean(boxInputErrors[key])}
                      aria-valuenow={parseBoxInput(key, boxInputs[key]).value ?? undefined}
                      aria-valuemin={key.startsWith("size_") ? minEditableBoxDimension : undefined}
                      aria-valuetext={`${boxInputs[key] || "未完成"} 埃`}
                      aria-describedby={`viewer-box-edit-help${boxInputErrors[key] ? ` ${errorId}` : ""}`}
                      aria-keyshortcuts="Enter Escape ArrowUp ArrowDown"
                    />
                    <button
                      className="viewer-wheel-bind-button"
                      type="button"
                      onClick={() => toggleWheelBinding(key)}
                      aria-pressed={isBound}
                      aria-controls="viewer-canvas"
                      aria-label={
                        isBound
                          ? `取消${boxFieldLabel(key)}的 3D 视图滚轮绑定`
                          : `将${boxFieldLabel(key)}绑定到 3D 视图滚轮`
                      }
                      title={isBound ? "取消滚轮绑定" : `用滚轮调整${boxFieldLabel(key)}`}
                    >
                      {isBound ? "已绑定" : "绑定"}
                    </button>
                  </div>
                  {boxInputErrors[key] ? (
                    <small className="viewer-box-error" id={errorId} role="alert">
                      {boxInputErrors[key]}
                    </small>
                  ) : null}
                </div>
                );
              })}
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
            <div className="viewer-box-commit-row" aria-live="polite">
              <StatusBadge
                tone={hasBoxInputErrors ? "error" : hasPendingBoxEdits || hasUnsavedBoxChanges ? "warning" : "ok"}
              >
                {hasBoxInputErrors
                  ? "输入待修正"
                  : hasPendingBoxEdits
                    ? "预览待应用"
                    : hasUnsavedBoxChanges
                      ? "预览尚未保存"
                      : "范围已保存"}
              </StatusBadge>
            </div>
            <div className="toolbar project-toolbar viewer-box-save-actions">
              <button
                className="secondary-button"
                type="button"
                disabled={isBusy || hasBoxInputErrors}
                onClick={applyPendingBox}
              >
                应用到预览
              </button>
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

        <section className="viewer-stage" aria-labelledby="viewer-stage-heading">
          <div className="viewer-stage-toolbar">
            <div className="viewer-stage-title">
              <span>当前视图</span>
              <strong id="viewer-stage-heading">{visibleLayerSummary}</strong>
            </div>
            <div className="viewer-stage-actions">
              <StatusBadge tone={isBoxVisible ? "info" : "muted"}>
                {isBoxVisible ? "Box 已显示" : "Box 已隐藏"}
              </StatusBadge>
              <StatusBadge tone={boundBoxField ? "warning" : "muted"}>
                {boundBoxField ? `滚轮绑定 ${boxFieldLabel(boundBoxField)}` : "滚轮缩放"}
              </StatusBadge>
              <button
                className="text-button inline"
                type="button"
                onClick={fitScene}
                disabled={!visibleLayerList.length && !isBoxVisible}
                aria-label="将全部可见结构和搜索范围适应到 3D 视图"
              >
                适应窗口
              </button>
              <button
                className="text-button inline"
                type="button"
                onClick={clearScene}
                disabled={!loadedLayerList.length}
                aria-label="清空全部结构图层，保留搜索范围"
              >
                清空视图
              </button>
            </div>
          </div>
          <div className={`viewer-canvas-shell ${boundBoxField ? "is-wheel-bound" : ""}`} ref={canvasShellRef}>
            <div
              className="viewer-canvas"
              id="viewer-canvas"
              ref={containerRef}
              role="region"
              tabIndex={0}
              aria-label={`3D 分子视图：${visibleLayerSummary}。视图内显示 X、Y、Z 方向轴。`}
              aria-describedby="viewer-canvas-help"
            />
            {!visibleLayerList.length && !isBoxVisible ? (
              <div className="viewer-canvas-empty" aria-hidden="true">
                <strong>等待加载结构</strong>
                <span>从右侧加载受体、配体或对接构象</span>
              </div>
            ) : null}
            <p className="viewer-canvas-help" id="viewer-canvas-help">
              {boundBoxField
                ? `滚轮调整${boxFieldLabel(boundBoxField)}（步进 ${boxStep} Å）· 拖动旋转 · Shift + 拖动平移`
                : "拖动旋转 · 滚轮缩放 · Shift + 拖动平移"}
            </p>
          </div>
        </section>

        <aside className="viewer-inspector-column" aria-label="文件、Box 与构象属性">
          <SectionCard className="viewer-dashboard-card viewer-dashboard-current" title="当前文件">
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

          <SectionCard className="viewer-dashboard-card viewer-dashboard-box" title="Box 摘要">
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
                <dt>体积</dt>
                <dd>{roundBoxValue(box.size_x * box.size_y * box.size_z).toLocaleString()} Å³</dd>
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

          <SectionCard className="viewer-dashboard-card viewer-dashboard-files" title="可查看文件">
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

          <div className="viewer-status-region" role="status" aria-live="polite" aria-atomic="true">
            <CommandResultPanel title="Viewer 状态" message={message} rawError={rawError} />
          </div>
        </aside>
      </section>
    </section>
  );
}
