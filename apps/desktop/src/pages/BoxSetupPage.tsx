import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import WarningCallout from "../components/WarningCallout";
import type { DockStartProject, ProjectResponse } from "../types";

type BoxSetupPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onProjectChange: (project: DockStartProject) => void;
  onOpenVinaParams: (project: DockStartProject) => void;
};

type BoxFormState = Record<keyof DockStartProject["box"], string>;

const boxFields: Array<{ key: keyof DockStartProject["box"]; label: string }> = [
  { key: "center_x", label: "中心 X" },
  { key: "center_y", label: "中心 Y" },
  { key: "center_z", label: "中心 Z" },
  { key: "size_x", label: "尺寸 X" },
  { key: "size_y", label: "尺寸 Y" },
  { key: "size_z", label: "尺寸 Z" },
];

function parseProjectResponse(rawPayload: string): ProjectResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ProjectResponse>;
  return {
    ok: Boolean(parsed.ok),
    project_dir: parsed.project_dir,
    project: parsed.project ?? null,
    box: parsed.box,
    warnings: parsed.warnings ?? [],
    message: parsed.message,
    error: parsed.error,
  };
}

function boxToForm(project: DockStartProject): BoxFormState {
  return {
    center_x: String(project.box.center_x),
    center_y: String(project.box.center_y),
    center_z: String(project.box.center_z),
    size_x: String(project.box.size_x),
    size_y: String(project.box.size_y),
    size_z: String(project.box.size_z),
  };
}

function hasPreparedFiles(project: DockStartProject): boolean {
  return Boolean(project.receptor.file && project.ligand.file);
}

function isValidBox(box: DockStartProject["box"]): boolean {
  const centerValues = [box.center_x, box.center_y, box.center_z];
  const sizeValues = [box.size_x, box.size_y, box.size_z];
  return centerValues.every(Number.isFinite) && sizeValues.every((value) => Number.isFinite(value) && value > 0);
}

export default function BoxSetupPage({
  project: initialProject,
  onBack,
  onProjectChange,
  onOpenVinaParams,
}: BoxSetupPageProps) {
  const [project, setProject] = useState<DockStartProject>(initialProject);
  const [boxForm, setBoxForm] = useState<BoxFormState>(() => boxToForm(initialProject));
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [canOpenVinaParams, setCanOpenVinaParams] = useState(false);

  const applyProjectResponse = useCallback(
    (response: ProjectResponse, fallbackMessage: string) => {
      if (response.ok && response.project) {
        setProject(response.project);
        setBoxForm(boxToForm(response.project));
        onProjectChange(response.project);
        setMessage(response.message ?? fallbackMessage);
        setWarnings(response.warnings ?? []);
        setRawError("");
        setCanOpenVinaParams(isValidBox(response.project.box));
        return;
      }
      setMessage(response.error?.message ?? "Box 参数保存失败。");
      setWarnings([]);
      setRawError(response.error?.raw_error ?? "");
      setCanOpenVinaParams(false);
    },
    [onProjectChange],
  );

  const reloadBox = useCallback(async () => {
    setIsBusy(true);
    setCanOpenVinaParams(false);
    try {
      const rawPayload = await invoke<string>("get_box_params", {
        projectDir: initialProject.project_dir,
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "Box 参数已刷新。");
    } catch (error) {
      setMessage("无法读取 Box 参数。");
      setWarnings([]);
      setRawError(error instanceof Error ? error.message : String(error));
      setCanOpenVinaParams(false);
    } finally {
      setIsBusy(false);
    }
  }, [applyProjectResponse, initialProject.project_dir]);

  useEffect(() => {
    void reloadBox();
  }, [reloadBox]);

  const updateField = (key: keyof DockStartProject["box"], value: string) => {
    setCanOpenVinaParams(false);
    setBoxForm((current) => ({ ...current, [key]: value }));
  };

  const saveBox = async () => {
    setIsBusy(true);
    setCanOpenVinaParams(false);
    setMessage("");
    setWarnings([]);
    setRawError("");
    try {
      const rawPayload = await invoke<string>("update_box_params", {
        projectDir: project.project_dir,
        boxJson: JSON.stringify(boxForm),
      });
      applyProjectResponse(parseProjectResponse(rawPayload), "搜索范围已保存。");
    } catch (error) {
      setMessage("无法保存搜索范围。");
      setRawError(error instanceof Error ? error.message : String(error));
      setCanOpenVinaParams(false);
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <PageShell labelledBy="box-setup-title">
      <PageHero
        eyebrow="工作流 3"
        title="设置搜索范围"
        titleId="box-setup-title"
        description="编辑 docking box 的中心和尺寸，单位为 Å。"
        actions={
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <div className="status-strip">
              <article className="metric-card">
                <span>受体 PDBQT</span>
                <strong>{project.receptor.file || "未导入"}</strong>
                <StatusBadge tone={project.receptor.file ? "ok" : "warning"}>{project.receptor.file ? "已完成" : "缺失"}</StatusBadge>
              </article>
              <article className="metric-card">
                <span>配体 PDBQT</span>
                <strong>{project.ligand.file || "未导入"}</strong>
                <StatusBadge tone={project.ligand.file ? "ok" : "warning"}>{project.ligand.file ? "已完成" : "缺失"}</StatusBadge>
              </article>
            </div>

            {!hasPreparedFiles(project) ? (
              <WarningCallout title="输入文件缺失">
                <p>可以先保存搜索范围，但运行对接前需要补全受体和配体 PDBQT。</p>
              </WarningCallout>
            ) : null}

            <SectionCard title="Box 参数">
              <div className="box-form">
                {boxFields.map((field) => (
                  <label className="box-field" key={field.key}>
                    <span>{field.label}</span>
                    <input
                      type="text"
                      value={boxForm[field.key]}
                      onChange={(event) => updateField(field.key, event.target.value)}
                      inputMode="decimal"
                    />
                  </label>
                ))}
              </div>
              <div className="button-row end">
                <ActionButton variant="text" disabled={isBusy} onClick={() => void reloadBox()}>重新加载</ActionButton>
                <ActionButton variant="primary" disabled={isBusy} onClick={() => void saveBox()}>
                  {isBusy ? "保存中..." : "保存搜索范围"}
                </ActionButton>
              </div>
            </SectionCard>

            <div className="next-step-strip">
              <div>
                <strong>{canOpenVinaParams ? "下一步：设置 Vina 参数" : "保存后继续设置 Vina 参数"}</strong>
                <p>Box 是搜索空间，不等于真实结合位点。</p>
              </div>
              <ActionButton variant="primary" disabled={!canOpenVinaParams} onClick={() => onOpenVinaParams(project)}>
                进入 Vina 参数
              </ActionButton>
            </div>

            {warnings.map((warning) => (
              <WarningCallout key={warning} title="搜索范围提示">
                <p>{warning}</p>
              </WarningCallout>
            ))}

            {message ? <p className="message-line">{message}</p> : null}
            {rawError ? (
              <AdvancedDetails>
                <pre>{rawError}</pre>
              </AdvancedDetails>
            ) : null}
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

          <RightRailSection title="搜索范围">
            <dl className="mode-context-list">
              <div>
                <dt>中心</dt>
                <dd>{project.box.center_x}, {project.box.center_y}, {project.box.center_z}</dd>
              </div>
              <div>
                <dt>尺寸</dt>
                <dd>{project.box.size_x}, {project.box.size_y}, {project.box.size_z}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="下一步">
            <p>{canOpenVinaParams ? "进入 Vina 参数设置。" : "保存搜索范围后继续。"}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
