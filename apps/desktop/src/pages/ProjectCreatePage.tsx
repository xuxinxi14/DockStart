import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import PathInput from "../components/PathInput";
import SectionCard from "../components/SectionCard";
import type { DockStartProject, ProjectResponse, SettingsResponse } from "../types";

type ProjectCreatePageProps = {
  onBack: () => void;
  onCreated: (project: DockStartProject, nextPage: "structure-fetch" | "import-pdbqt") => void;
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

function parseSettingsResponse(rawPayload: string): SettingsResponse {
  const parsed = JSON.parse(rawPayload) as Partial<SettingsResponse>;
  return {
    ok: Boolean(parsed.ok),
    settings_path: parsed.settings_path ?? "",
    settings: parsed.settings ?? null,
    error: parsed.error,
  };
}

export default function ProjectCreatePage({ onBack, onCreated }: ProjectCreatePageProps) {
  const [projectName, setProjectName] = useState("demo_project");
  const [baseDir, setBaseDir] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  useEffect(() => {
    async function loadDefaultProjectDir() {
      try {
        const rawPayload = await invoke<string>("get_settings");
        const response = parseSettingsResponse(rawPayload);
        if (response.ok && response.settings?.project.default_project_dir) {
          setBaseDir(response.settings.project.default_project_dir);
        }
      } catch {
        // Default directory is optional.
      }
    }

    void loadDefaultProjectDir();
  }, []);

  const createProject = useCallback(async () => {
    setIsBusy(true);
    setMessage("");
    setRawError("");
    try {
      const rawPayload = await invoke<string>("create_project", {
        projectName,
        baseDir,
      });
      const response = parseProjectResponse(rawPayload);
      if (response.ok && response.project) {
        onCreated(response.project, "structure-fetch");
        return;
      }
      setMessage(response.error?.message ?? "项目创建失败。");
      setRawError(response.error?.raw_error ?? "");
    } catch (error) {
      setMessage("无法创建项目。请确认当前运行环境是 DockStart 桌面端。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [baseDir, onCreated, projectName]);

  return (
    <section className="workbench-page" aria-labelledby="project-create-title">
      <header className="page-hero">
        <div className="page-hero-main">
          <p className="eyebrow">项目</p>
          <h1 id="project-create-title">创建项目</h1>
          <p>创建标准项目目录和 project.json，随后进入结构获取步骤。</p>
        </div>
        <div className="page-hero-actions">
          <ActionButton variant="text" onClick={onBack}>返回</ActionButton>
        </div>
      </header>

      <div className="task-layout">
        <main className="task-main">
          <SectionCard title="项目信息">
            <div className="form-panel">
              <label htmlFor="project-name">项目名称</label>
              <input
                id="project-name"
                type="text"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="例如 demo_project"
              />

              <label htmlFor="base-dir">保存目录</label>
              <PathInput
                id="base-dir"
                value={baseDir}
                onChange={setBaseDir}
                mode="directory"
                title="选择项目保存目录"
                placeholder="选择项目的父目录"
              />

              <div className="button-row end">
                <ActionButton variant="primary" disabled={isBusy} onClick={() => void createProject()}>
                  {isBusy ? "创建中..." : "创建项目"}
                </ActionButton>
              </div>
            </div>
          </SectionCard>

          {message ? <p className="message-line">{message}</p> : null}
          {rawError ? (
            <AdvancedDetails>
              <pre>{rawError}</pre>
            </AdvancedDetails>
          ) : null}
        </main>

        <aside className="task-context">
          <SectionCard title="下一步">
            <div className="next-step-strip">
              <div>
                <strong>获取结构</strong>
                <p>也可以稍后手动导入已经准备好的 PDBQT。</p>
              </div>
            </div>
          </SectionCard>
        </aside>
      </div>
    </section>
  );
}
