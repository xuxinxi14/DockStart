import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import PathInput from "../components/PathInput";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import type { PageId } from "../navigation/pages";
import type { DemoProjectsResponse, DockStartProject, ProjectResponse, SettingsResponse } from "../types";

type ProjectCreatePageProps = {
  onBack: () => void;
  onCreated: (project: DockStartProject, nextPage: PageId) => void;
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

function parseDemoProjectsResponse(rawPayload: string): DemoProjectsResponse {
  const parsed = JSON.parse(rawPayload) as Partial<DemoProjectsResponse>;
  return {
    ok: Boolean(parsed.ok),
    examples_root: parsed.examples_root ?? "",
    demos: parsed.demos ?? [],
    message: parsed.message ?? "",
    error: parsed.error ?? null,
  };
}

function nextPageForDemo(demoType: string): PageId {
  if (demoType === "basic_pdbqt" || demoType === "viewer_only") return "import-pdbqt";
  return "structure-fetch";
}

export default function ProjectCreatePage({ onBack, onCreated }: ProjectCreatePageProps) {
  const [projectName, setProjectName] = useState("demo_project");
  const [baseDir, setBaseDir] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [demos, setDemos] = useState<DemoProjectsResponse["demos"]>([]);

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

  useEffect(() => {
    async function loadDemos() {
      try {
        const rawPayload = await invoke<string>("list_available_demo_projects");
        const response = parseDemoProjectsResponse(rawPayload);
        if (response.ok) {
          setDemos(response.demos.filter((demo) => demo.exists));
        }
      } catch {
        setDemos([]);
      }
    }

    void loadDemos();
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

  const createDemo = useCallback(
    async (demoType: string) => {
      setIsBusy(true);
      setMessage("");
      setRawError("");
      try {
        const rawPayload = await invoke<string>("create_demo_project", {
          destinationDir: baseDir,
          demoType,
        });
        const response = parseProjectResponse(rawPayload);
        if (response.ok && response.project) {
          onCreated(response.project, nextPageForDemo(demoType));
          return;
        }
        setMessage(response.error?.message ?? "示例项目创建失败。");
        setRawError(response.error?.raw_error ?? "");
      } catch (error) {
        setMessage("无法创建示例项目。请确认当前运行环境是 DockStart 桌面端。");
        setRawError(error instanceof Error ? error.message : String(error));
      } finally {
        setIsBusy(false);
      }
    },
    [baseDir, onCreated],
  );

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

          <SectionCard title="示例项目">
            <p className="placeholder-note">
              示例只用于软件流程演示，不用于科研结论。请选择保存目录后复制一份示例到你的工作区。
            </p>
            <div className="compact-grid">
              {demos.map((demo) => (
                <article className="metric-card" key={demo.demo_type}>
                  <span>{demo.demo_type}</span>
                  <strong>{demo.title}</strong>
                  <p>{demo.description}</p>
                  <StatusBadge tone="warning">仅演示流程</StatusBadge>
                  <ActionButton disabled={isBusy || !baseDir.trim()} onClick={() => void createDemo(demo.demo_type)}>
                    复制示例项目
                  </ActionButton>
                </article>
              ))}
              {!demos.length ? <p className="placeholder-note">没有检测到可用示例项目资源。</p> : null}
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
                <strong>选择路径</strong>
                <p>Basic Mode 可直接导入 PDBQT；Assisted Mode 可从 raw 文件开始；Demo Mode 可先复制示例。</p>
              </div>
            </div>
          </SectionCard>
        </aside>
      </div>
    </section>
  );
}
