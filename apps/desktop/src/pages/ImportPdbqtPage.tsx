import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import BasicModeGuide from "../components/BasicModeGuide";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import OperationLoadingDialog from "../components/OperationLoadingDialog";
import PathInput from "../components/PathInput";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, PreparationStatusResponse, ProjectFileRef, ProjectResponse } from "../types";
import { writeDockingWorkspaceMode } from "../utils/dockingMode";
import { normalizeLigandImportPreview } from "../utils/screeningLigandImport";

type ImportPdbqtPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenStructureFetch: (project: DockStartProject) => void;
  onOpenBoxSetup: (project: DockStartProject) => void;
  onProjectChange: (project: DockStartProject) => void;
};

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    message: parsed.message,
    error: parsed.error,
  };
}

function fileText(fileRef: ProjectFileRef): string {
  return fileRef.file || "未导入";
}

function projectStagedFilePath(projectDir: string, stagedFile: string): string {
  if (/^(?:[A-Za-z]:[\\/]|\\\\)/.test(stagedFile)) return stagedFile;
  return `${projectDir.replace(/[\\/]+$/, "")}\\${stagedFile.replace(/^[\\/]+/, "").replace(/\//g, "\\")}`;
}

function noReadyLigandMessage(): string {
  return "导入结果中没有可用配体。重复、准备失败或需要正式大环审查的记录不会进入任务；请检查导入结果后重新选择。";
}

export default function ImportPdbqtPage({
  project: initialProject,
  onBack,
  onOpenStructureFetch,
  onOpenBoxSetup,
  onProjectChange,
}: ImportPdbqtPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [receptorPath, setReceptorPath] = useState("");
  const [ligandPaths, setLigandPaths] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [busyOperation, setBusyOperation] = useState<{
    title: string;
    message: string;
  } | null>(null);
  const [readyFiles, setReadyFiles] = useState({ receptor: false, ligand: false });

  useEffect(() => {
    setProject(initialProject);
  }, [initialProject]);

  const applyProjectResponse = (response: ProjectResponse, fallbackMessage: string) => {
    if (response.ok && response.project) {
      setProject(response.project);
      onProjectChange(response.project);
      setMessage(response.message ?? fallbackMessage);
      setRawError("");
      return;
    }
    setMessage(response.error?.message ?? "导入失败。");
    setRawError(response.error?.raw_error ?? "");
  };

  const refreshPreparedStatus = useCallback(async () => {
    const rawPayload = await invoke<string>("get_preparation_status", { projectDir: project.project_dir });
    const parsed = JSON.parse(rawPayload) as Partial<PreparationStatusResponse>;
    setReadyFiles({
      receptor: parsed.files?.receptor_prepared?.status === "ok",
      ligand: parsed.files?.ligand_prepared?.status === "ok",
    });
  }, [project.project_dir]);

  useEffect(() => {
    void refreshPreparedStatus().catch(() => {
      setReadyFiles({ receptor: false, ligand: false });
    });
  }, [refreshPreparedStatus]);

  const reloadProject = useCallback(async () => {
    setIsBusy(true);
    setBusyOperation({
      title: "正在刷新项目",
      message: "正在核对受体、配体与项目记录。",
    });
    try {
      const rawPayload = await invoke<string>("load_project", {
        projectDir: project.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "项目已刷新。");
      await refreshPreparedStatus();
    } catch (error) {
      setMessage("无法读取项目。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyOperation(null);
      setIsBusy(false);
    }
  }, [project.project_dir, refreshPreparedStatus]);

  const importFile = async (role: "receptor" | "ligand") => {
    setIsBusy(true);
    setBusyOperation({
      title: role === "receptor" ? "正在导入受体 PDBQT" : "正在导入配体 PDBQT",
      message: role === "ligand" && ligandPaths.length > 1
        ? `正在复制并检查 ${ligandPaths.length} 个配体文件。`
        : "正在复制文件并更新项目记录。",
    });
    setMessage("");
    setRawError("");
    try {
      let readyLigandCount = 0;
      let sourcePath = receptorPath;
      if (role === "ligand") {
        const stageResponse = JSON.parse(await invoke<string>("stage_screening_inputs", {
          projectDir: project.project_dir,
          files: ligandPaths,
        })) as {
          ok?: boolean;
          error?: { message?: string; raw_error?: string };
          [key: string]: unknown;
        };
        if (!stageResponse.ok) {
          throw new Error(stageResponse.error?.raw_error || stageResponse.error?.message || "配体快照导入失败。");
        }
        const preview = normalizeLigandImportPreview(stageResponse);
        const firstReady = preview.candidates.find(
          (candidate) => candidate.status === "ready" && candidate.stagedFile,
        );
        readyLigandCount = preview.counts.ready;
        if (readyLigandCount === 0 || !firstReady?.stagedFile) {
          throw new Error(noReadyLigandMessage());
        }
        sourcePath = projectStagedFilePath(project.project_dir, firstReady.stagedFile);
      }
      const rawPayload = await invoke<string>(role === "receptor" ? "import_receptor_pdbqt" : "import_ligand_pdbqt", {
        projectDir: project.project_dir,
        sourcePath,
      });
      const response = parseProjectResponse(rawPayload);
      applyProjectResponse(response, role === "receptor" ? "受体 PDBQT 已导入。" : "配体 PDBQT 已导入。");
      if (response.ok && role === "ligand") {
        if (readyLigandCount >= 2) {
          writeDockingWorkspaceMode(project.project_dir, "batch");
          setMessage(`已导入 ${readyLigandCount} 个可用配体，并自动切换为串行批量筛选；首个可用配体用于搜索范围预览。`);
        } else {
          writeDockingWorkspaceMode(project.project_dir, "single");
          setMessage("已导入 1 个可用配体，并保持单配体任务。");
        }
      }
      if (response.ok) await refreshPreparedStatus();
    } catch (error) {
      setMessage(role === "receptor" ? "无法导入受体 PDBQT。" : "无法导入配体 PDBQT。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyOperation(null);
      setIsBusy(false);
    }
  };

  const chooseLigands = async () => {
    const selected = await open({
      directory: false,
      multiple: true,
      title: "选择一个或多个配体 PDBQT",
      filters: [{ name: "AutoDock PDBQT", extensions: ["pdbqt"] }],
    });
    const files = Array.isArray(selected) ? selected : selected ? [selected] : [];
    if (files.length) setLigandPaths(files);
  };

  const readyForBox = readyFiles.receptor && readyFiles.ligand;

  const renderImportCard = (role: "receptor" | "ligand") => {
    const isReceptor = role === "receptor";
    const fileRef = isReceptor ? project.receptor : project.ligand;
    const sourcePath = isReceptor ? receptorPath : ligandPaths[0] ?? "";
    const isReady = isReceptor ? readyFiles.receptor : readyFiles.ligand;
    return (
      <article className="task-card" data-layout="task-card">
        <div className="section-card-header">
          <h2>{isReceptor ? "受体 PDBQT" : "配体 PDBQT"}</h2>
          <StatusBadge tone={isReady ? "ok" : "warning"}>{isReady ? "已完成" : "缺失"}</StatusBadge>
        </div>
        <p className="muted-path">{isReady ? fileText(fileRef) : "未导入可用 PDBQT"}</p>
        {isReceptor ? (
          <PathInput
            value={sourcePath}
            onChange={setReceptorPath}
            mode="file"
            title="选择受体 PDBQT 文件"
            placeholder="选择 receptor.pdbqt"
            ariaLabel="受体 PDBQT 源文件路径"
            filters={[{ name: "PDBQT", extensions: ["pdbqt"] }]}
          />
        ) : (
          <div className="multi-ligand-file-picker">
            <ActionButton onClick={() => void chooseLigands()}>选择一个或多个 PDBQT</ActionButton>
            <span>
              {ligandPaths.length
                ? `已选择 ${ligandPaths.length} 个文件；导入后按可用配体数量决定单配体或批量筛选`
                : "可选择一个或多个文件；只有实际可用配体不少于 2 个时才进入批量筛选"}
            </span>
            {ligandPaths.length ? <code title={ligandPaths.join("\n")}>{ligandPaths.map((path) => path.split(/[\\/]/).pop()).join("、")}</code> : null}
          </div>
        )}
        <ActionButton variant="primary" disabled={isBusy || !sourcePath.trim()} onClick={() => void importFile(role)}>
          {isReceptor ? "导入受体" : ligandPaths.length > 1 ? `导入 ${ligandPaths.length} 个配体` : "导入配体"}
        </ActionButton>
      </article>
    );
  };

  return (
    <PageShell labelledBy="import-pdbqt-title">
      <OperationLoadingDialog
        open={Boolean(busyOperation)}
        title={busyOperation?.title ?? ""}
        message={busyOperation?.message ?? ""}
        detail="文件只会写入当前项目。"
      />
      <PageHero
        eyebrow="Vina 输入"
        title="导入已有 PDBQT"
        titleId="import-pdbqt-title"
        description="适合已经准备好 receptor.pdbqt 与 ligand.pdbqt 的用户；如果只有原始结构，可切换到在线搜索或格式转换。"
        actions={
          <>
          <ActionButton variant="primary" onClick={() => onOpenStructureFetch(project)}>在线搜索 / 导入原始结构</ActionButton>
          <ActionButton variant="text" onClick={onBack}>返回格式转换</ActionButton>
          <ActionButton onClick={() => void reloadProject()} disabled={isBusy}>刷新项目</ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <WarningCallout title="PDBQT 是 Vina 输入">
              <p>raw 文件需要先准备成 PDBQT，才能进入 Box 和运行步骤。</p>
            </WarningCallout>

            <BasicModeGuide compact primaryLabel="导入 PDBQT 后设置搜索范围" />

            <div className="two-column-grid">
              {renderImportCard("receptor")}
              {renderImportCard("ligand")}
            </div>

            <div className="next-step-strip">
              <div>
                <strong>{readyForBox ? "下一步：设置搜索范围" : "先补全受体和配体"}</strong>
                <p>只有 PDB/CIF、SDF/MOL？切换到原始结构入口并转换为 PDBQT。</p>
              </div>
              <div className="button-row end">
                <ActionButton onClick={() => onOpenStructureFetch(project)}>在线搜索或导入原始结构</ActionButton>
                <ActionButton variant="primary" disabled={!readyForBox} onClick={() => onOpenBoxSetup(project)}>
                  设置搜索范围
                </ActionButton>
              </div>
            </div>

            {message ? <p className="message-line">{message}</p> : null}
            {rawError ? (
              <AdvancedDetails>
                <pre>{rawError}</pre>
              </AdvancedDetails>
            ) : null}
            <SectionCard title="技术说明">
              <p>导入时文件会复制到项目 prepared 目录，并更新 project.json。</p>
            </SectionCard>
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="输入状态">
            <dl className="mode-context-list">
              <div>
                <dt>受体</dt>
                <dd>{readyFiles.receptor ? "已导入" : "缺失"}</dd>
              </div>
              <div>
                <dt>配体</dt>
                <dd>{readyFiles.ligand ? "已导入" : "缺失"}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>{readyForBox ? "进入搜索范围设置。" : "先补全受体和配体 PDBQT。"}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
