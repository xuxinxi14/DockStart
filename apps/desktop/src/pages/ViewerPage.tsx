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
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";

type ViewerPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
};

const fileKindOptions: Array<{ value: ViewerFileKind; label: string; description: string }> = [
  { value: "receptor_raw", label: "受体 raw", description: "project.json 中的 receptor.raw_file" },
  { value: "ligand_raw", label: "配体 raw", description: "project.json 中的 ligand.raw_file" },
  { value: "receptor_prepared", label: "受体 prepared", description: "prepared/receptor.pdbqt" },
  { value: "ligand_prepared", label: "配体 prepared", description: "prepared/ligand.pdbqt" },
  { value: "docking_output", label: "docking output", description: "最近 run 的 out.pdbqt" },
];

const viewerFormats = new Set(["pdb", "pdbqt", "cif", "sdf", "mol", "mol2"]);

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
      color: "orange",
      alpha: 0.16,
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

export default function ViewerPage({ project, onBack, onProjectChange }: ViewerPageProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ReturnType<typeof $3Dmol.createViewer> | null>(null);
  const [fileKind, setFileKind] = useState<ViewerFileKind>("receptor_prepared");
  const [box, setBox] = useState(project.box);
  const [boxVisualization, setBoxVisualization] = useState<BoxVisualizationPayload | null>(() =>
    buildLocalBoxVisualization(project.box),
  );
  const [boxWarnings, setBoxWarnings] = useState<string[]>([]);
  const [runId, setRunId] = useState(() => latestRunId(project));
  const [poseList, setPoseList] = useState<DockingPoseListResponse | null>(null);
  const [selectedPose, setSelectedPose] = useState<DockingPoseSummary | null>(null);
  const [status, setStatus] = useState<ViewerFileStatusResponse | null>(null);
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

  const zoomToFit = useCallback(() => {
    viewerRef.current?.zoomTo();
    viewerRef.current?.render();
  }, []);

  const renderStructure = useCallback(
    (nextStructure: ViewerStructureResult) => {
      clearViewer();
      if (!containerRef.current) {
        return;
      }
      if (!viewerFormats.has(nextStructure.format)) {
        setMessage("结构文件已读取，但当前 viewer 还不能确认该格式可直接显示。");
        return;
      }

      try {
        const viewer = $3Dmol.createViewer(containerRef.current, { backgroundColor: "white" });
        viewer.addModel(nextStructure.content, nextStructure.format);
        if (nextStructure.file_kind.includes("receptor")) {
          viewer.setStyle({}, { cartoon: { color: "spectrum" }, stick: { radius: 0.12 } });
        } else {
          viewer.setStyle({}, { stick: { radius: 0.24, colorscheme: "greenCarbon" } });
        }
        if (boxVisualization) {
          viewer.addBox(boxVisualization.viewer_box_payload);
        }
        viewer.zoomTo();
        viewer.render();
        viewerRef.current = viewer;
      } catch (error) {
        setMessage("3Dmol.js 未能显示该结构文件。文件已读取，但格式或内容可能不被当前 viewer 支持。");
        setRawError(error instanceof Error ? error.message : String(error));
      }
    },
    [boxVisualization, clearViewer],
  );

  const renderPoseWithReceptor = useCallback(
    async (poseStructure: ViewerStructureResult) => {
      clearViewer();
      if (!containerRef.current) {
        return;
      }
      try {
        const viewer = $3Dmol.createViewer(containerRef.current, { backgroundColor: "white" });
        try {
          const rawReceptor = await invoke<string>("load_structure_for_viewer", {
            projectDir: project.project_dir,
            fileKind: "receptor_prepared",
          });
          const receptor = parseViewerStructure(rawReceptor);
          if (receptor.ok && viewerFormats.has(receptor.format)) {
            const receptorModel = viewer.addModel(receptor.content, receptor.format);
            receptorModel.setStyle({}, { cartoon: { color: "spectrum", opacity: 0.72 }, stick: { radius: 0.08 } });
          }
        } catch {
          // Pose viewing still works without a prepared receptor reference.
        }

        const poseModel = viewer.addModel(poseStructure.content, poseStructure.format);
        poseModel.setStyle({}, { stick: { radius: 0.26, colorscheme: "magentaCarbon" } });
        if (boxVisualization) {
          viewer.addBox(boxVisualization.viewer_box_payload);
        }
        viewer.zoomTo();
        viewer.render();
        viewerRef.current = viewer;
      } catch (error) {
        setMessage("3Dmol.js 未能显示该 docking pose。");
        setRawError(error instanceof Error ? error.message : String(error));
      }
    },
    [boxVisualization, clearViewer, project.project_dir],
  );

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
      const rawPayload = await invoke<string>("load_structure_for_viewer", {
        projectDir: project.project_dir,
        fileKind,
      });
      const parsed = parseViewerStructure(rawPayload);
      setStructure(parsed);
      setSelectedPose(null);
      if (!parsed.ok) {
        clearViewer();
        setMessage(parsed.error?.message ?? parsed.message ?? "结构文件无法加载。");
        setRawError(parsed.error?.raw_error ?? "");
        return;
      }
      setMessage(parsed.message || "结构文件已加载。");
      renderStructure(parsed);
    } catch (error) {
      clearViewer();
      setMessage("前端未能调用结构读取命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [clearViewer, fileKind, project.project_dir, renderStructure]);

  const loadPoseList = useCallback(async () => {
    if (!runId.trim()) {
      setMessage("请先输入 run_id，例如 run_001。");
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
        setMessage(parsed.error?.message ?? "pose 列表读取失败。");
        setRawError(parsed.error?.raw_error ?? "");
        return;
      }
      setMessage(parsed.message ?? "pose 列表已读取。");
    } catch (error) {
      setMessage("前端未能调用 pose 列表命令。");
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
          setMessage(parsed.error?.message ?? "docking pose 读取失败。");
          setRawError(parsed.error?.raw_error ?? "");
          return;
        }
        setStructure(parsed);
        setSelectedPose(poseList?.poses.find((pose) => pose.mode === mode) ?? null);
        setFileKind("docking_output");
        setMessage("已加载 docking pose。Docking pose 和 docking score 仅供结构查看与趋势参考，不能证明真实结合或药效。");
        await renderPoseWithReceptor(parsed);
      } catch (error) {
        setMessage("前端未能调用 docking pose 读取命令。");
        setRawError(error instanceof Error ? error.message : String(error));
      } finally {
        setIsBusy(false);
      }
    },
    [poseList?.poses, project.project_dir, renderPoseWithReceptor, runId],
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
    const next = { ...box, [key]: value };
    setBox(next);
    setBoxVisualization(buildLocalBoxVisualization(next));
    setBoxWarnings(localBoxWarnings(next));
  };

  useEffect(() => {
    void reloadStatus();
    void loadBoxVisualization();
    return () => {
      clearViewer();
    };
  }, [clearViewer, loadBoxVisualization, reloadStatus]);

  useEffect(() => {
    if (structure?.ok) {
      if (selectedPose && structure.file_kind === "docking_output") {
        void renderPoseWithReceptor(structure);
        return;
      }
      renderStructure(structure);
    }
  }, [boxVisualization, renderPoseWithReceptor, renderStructure, selectedPose, structure]);

  const selectedStatus = status?.files?.[fileKind];
  const selectedOption = fileKindOptions.find((item) => item.value === fileKind);

  return (
    <section className="project-page viewer-page">
      <PageHeader
        eyebrow="ViewerPage"
        title="3D 结构、Box 与 pose 工作区"
        description="查看项目内 raw、prepared 和 docking output 结构文件，并把 Box 可视化设置同步回 project.json。当前只做几何查看，不做相互作用分析、pocket prediction 或药效判断。"
        actions={
          <button className="text-button" type="button" onClick={onBack}>
            返回上一页
          </button>
        }
      />

      <div className="project-summary">
        <span>当前项目</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <ScientificDisclaimer kind="viewer" />

      <section className="viewer-workspace-grid" aria-label="3D viewer workspace">
        <aside className="viewer-control-column" aria-label="Viewer controls">
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
            <div className="toolbar project-toolbar">
              <button className="primary-button" type="button" disabled={isBusy} onClick={() => void loadStructure()}>
                加载结构
              </button>
              <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void reloadStatus()}>
                重新读取状态
              </button>
            </div>
          </SectionCard>

          <SectionCard
            title="Docking pose"
            description="读取 runs/<run_id>/out.pdbqt 中的 mode。score 只用于查看构象列表，不代表真实结合或药效。"
          >
            <label className="viewer-source-row">
              <span>run_id</span>
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
                读取 pose 列表
              </button>
            </div>
            {poseList?.warnings?.length ? (
              <WarningCallout title="Pose 读取提示">
                {poseList.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </WarningCallout>
            ) : null}
          </SectionCard>

          <SectionCard
            title="Box 可视化设置"
            description="单位：Å。Box 可视化只是帮助定位搜索空间，不代表自动识别结合口袋。"
          >
            <div className="box-control-grid">
              {(["center_x", "center_y", "center_z", "size_x", "size_y", "size_z"] as Array<
                keyof DockStartProject["box"]
              >).map((key) => (
                <label key={key} className="box-control">
                  <span>{key}</span>
                  <input
                    type="number"
                    step="0.1"
                    value={box[key]}
                    onChange={(event) => updateBoxField(key, Number(event.target.value))}
                  />
                </label>
              ))}
            </div>
            <div className="toolbar project-toolbar">
              <button className="primary-button" type="button" disabled={isBusy} onClick={() => void saveBox()}>
                保存 Box 参数
              </button>
              <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void loadBoxVisualization()}>
                重新读取 Box
              </button>
            </div>
            {(boxWarnings.length ? boxWarnings : localBoxWarnings(box)).map((warning) => (
              <WarningCallout key={warning} title="Box 尺寸提示">
                <p>{warning}</p>
              </WarningCallout>
            ))}
          </SectionCard>
        </aside>

        <section className="viewer-stage" aria-label="3D molecular viewer">
          <div className="viewer-stage-toolbar">
            <div>
              <span>当前视图</span>
              <strong>{selectedPose ? `mode ${selectedPose.mode}` : selectedOption?.label}</strong>
            </div>
            <div className="toolbar project-toolbar">
              <button className="text-button inline" type="button" onClick={zoomToFit}>
                重新居中
              </button>
              <button className="text-button inline" type="button" onClick={clearViewer}>
                清空 viewer
              </button>
            </div>
          </div>
          <div className="viewer-canvas" ref={containerRef} aria-label="3D molecular viewer canvas" />
          <WarningCallout title="Viewer 边界">
            <p>只显示几何结构、Box 和 pose，不自动解释氢键、疏水、盐桥或药效。</p>
          </WarningCallout>
        </section>

        <aside className="viewer-inspector-column" aria-label="Viewer inspector">
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
            </dl>
            {structure?.warnings?.length ? (
              <WarningCallout title="结构文件提示">
                {structure.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </WarningCallout>
            ) : null}
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
                      <th>mode</th>
                      <th>affinity</th>
                      <th>rmsd_lb</th>
                      <th>rmsd_ub</th>
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
              <p className="placeholder-note">尚未读取 pose 列表。</p>
            )}
            <dl className="tool-meta">
              <div>
                <dt>当前 pose</dt>
                <dd>
                  {selectedPose
                    ? `mode ${selectedPose.mode}, affinity ${selectedPose.affinity_kcal_mol ?? "未解析"}`
                    : "未选择"}
                </dd>
              </div>
            </dl>
          </SectionCard>

          <CommandResultPanel title="Viewer 结果" message={message} rawError={rawError} />
        </aside>
      </section>
    </section>
  );
}
