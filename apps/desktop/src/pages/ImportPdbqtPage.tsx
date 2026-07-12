import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import BasicModeGuide from "../components/BasicModeGuide";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import PathInput from "../components/PathInput";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectFileRef, ProjectResponse } from "../types";

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

export default function ImportPdbqtPage({
  project: initialProject,
  onBack,
  onOpenStructureFetch,
  onOpenBoxSetup,
  onProjectChange,
}: ImportPdbqtPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [receptorPath, setReceptorPath] = useState("");
  const [ligandPath, setLigandPath] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);

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

  const reloadProject = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("load_project", {
        projectDir: project.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "项目已刷新。");
    } catch (error) {
      setMessage("无法读取项目。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [project.project_dir]);

  const importFile = async (role: "receptor" | "ligand") => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>(role === "receptor" ? "import_receptor_pdbqt" : "import_ligand_pdbqt", {
        projectDir: project.project_dir,
        sourcePath: role === "receptor" ? receptorPath : ligandPath,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), role === "receptor" ? "受体 PDBQT 已导入。" : "配体 PDBQT 已导入。");
    } catch (error) {
      setMessage(role === "receptor" ? "无法导入受体 PDBQT。" : "无法导入配体 PDBQT。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const readyForBox = Boolean(project.receptor.file && project.ligand.file);

  const renderImportCard = (role: "receptor" | "ligand") => {
    const isReceptor = role === "receptor";
    const fileRef = isReceptor ? project.receptor : project.ligand;
    const sourcePath = isReceptor ? receptorPath : ligandPath;
    const setSourcePath = isReceptor ? setReceptorPath : setLigandPath;
    return (
      <article className="task-card" data-layout="task-card">
        <div className="section-card-header">
          <h2>{isReceptor ? "受体 PDBQT" : "配体 PDBQT"}</h2>
          <StatusBadge tone={fileRef.file ? "ok" : "warning"}>{fileRef.file ? "已完成" : "缺失"}</StatusBadge>
        </div>
        <p className="muted-path">{fileText(fileRef)}</p>
        <PathInput
          value={sourcePath}
          onChange={setSourcePath}
          mode="file"
          title={isReceptor ? "选择受体 PDBQT 文件" : "选择配体 PDBQT 文件"}
          placeholder={isReceptor ? "选择 receptor.pdbqt" : "选择 ligand.pdbqt"}
          ariaLabel={isReceptor ? "受体 PDBQT 源文件路径" : "配体 PDBQT 源文件路径"}
          filters={[{ name: "PDBQT", extensions: ["pdbqt"] }]}
        />
        <ActionButton variant="primary" disabled={isBusy || !sourcePath.trim()} onClick={() => void importFile(role)}>
          {isReceptor ? "导入受体" : "导入配体"}
        </ActionButton>
      </article>
    );
  };

  return (
    <PageShell labelledBy="import-pdbqt-title">
      <PageHero
        eyebrow="Vina 输入"
        title="导入 PDBQT"
        titleId="import-pdbqt-title"
        description="选择已经准备好的受体和配体 PDBQT 文件。"
        actions={
          <>
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
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
                <p>也可以回到结构获取页下载 raw 文件。</p>
              </div>
              <div className="button-row end">
                <ActionButton onClick={() => onOpenStructureFetch(project)}>获取结构</ActionButton>
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
                <dd>{project.receptor.file ? "已导入" : "缺失"}</dd>
              </div>
              <div>
                <dt>配体</dt>
                <dd>{project.ligand.file ? "已导入" : "缺失"}</dd>
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
