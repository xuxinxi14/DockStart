import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import * as $3Dmol from "3dmol";
import type { DockStartProject, ViewerFileKind, ViewerFileStatusResponse, ViewerStructureResult } from "../types";

type ViewerPageProps = {
  project: DockStartProject;
  onBack: () => void;
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

export default function ViewerPage({ project, onBack }: ViewerPageProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ReturnType<typeof $3Dmol.createViewer> | null>(null);
  const [fileKind, setFileKind] = useState<ViewerFileKind>("receptor_prepared");
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
        viewer.zoomTo();
        viewer.render();
        viewerRef.current = viewer;
      } catch (error) {
        setMessage("3Dmol.js 未能显示该结构文件。文件已读取，但格式或内容可能不被当前 viewer 支持。");
        setRawError(error instanceof Error ? error.message : String(error));
      }
    },
    [clearViewer],
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

  useEffect(() => {
    void reloadStatus();
    return () => {
      clearViewer();
    };
  }, [clearViewer, reloadStatus]);

  const selectedStatus = status?.files?.[fileKind];
  const selectedOption = fileKindOptions.find((item) => item.value === fileKind);

  return (
    <section className="project-page" aria-labelledby="viewer-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回上一页
      </button>

      <div className="page-heading">
        <p className="eyebrow">ViewerPage</p>
        <h1 id="viewer-title">3D 结构查看</h1>
        <p>
          V0.4.1 提供最小 3D 查看入口，用于查看项目内 raw、prepared 和 docking output 结构文件。
          当前只做几何查看，不做相互作用分析、pocket prediction 或药效判断。
        </p>
      </div>

      <div className="project-summary">
        <span>当前项目</span>
        <strong>{project.project_name}</strong>
        <code>{project.project_dir}</code>
      </div>

      <div className="warning-note">
        3D 显示只帮助检查文件和空间位置。Docking pose 和 docking score 不能证明真实结合或药效，也不能替代专业分子建模检查。
      </div>

      <section className="panel viewer-controls" aria-label="Viewer controls">
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
        <p className="placeholder-note">{selectedOption?.description}</p>
        <div className="toolbar project-toolbar">
          <button className="primary-button" type="button" disabled={isBusy} onClick={() => void loadStructure()}>
            加载结构
          </button>
          <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void reloadStatus()}>
            重新读取状态
          </button>
          <button className="text-button inline" type="button" onClick={zoomToFit}>
            重新居中
          </button>
          <button className="text-button inline" type="button" onClick={clearViewer}>
            清空 viewer
          </button>
        </div>
      </section>

      <section className="viewer-layout">
        <div className="viewer-canvas" ref={containerRef} aria-label="3D molecular viewer" />
        <aside className="panel viewer-side-panel">
          <h2>文件状态</h2>
          <dl className="tool-meta">
            <div>
              <dt>当前选择</dt>
              <dd>{selectedOption?.label}</dd>
            </div>
            <div>
              <dt>状态</dt>
              <dd>{displayStatus(selectedStatus)}</dd>
            </div>
            <div>
              <dt>路径</dt>
              <dd><code>{structure?.relative_path || selectedStatus?.relative_path || "未记录"}</code></dd>
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
          {message ? <div className="settings-message">{message}</div> : null}
          {structure?.warnings?.length ? (
            <div className="warning-note">
              {structure.warnings.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          ) : null}
          {rawError ? (
            <details className="raw-error">
              <summary>查看原始错误</summary>
              <pre>{rawError}</pre>
            </details>
          ) : null}
        </aside>
      </section>
    </section>
  );
}
