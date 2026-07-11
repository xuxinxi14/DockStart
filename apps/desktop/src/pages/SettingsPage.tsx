import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import PathInput from "../components/PathInput";
import SectionCard from "../components/SectionCard";
import type { DockStartSettings, SettingsResponse } from "../types";

type SettingsPageProps = {
  onBack: () => void;
};

const emptySettings: DockStartSettings = {
  tool_paths: {
    vina: "",
    python: "",
  },
  project: {
    default_project_dir: "",
  },
};

function normalizeSettings(settings: Partial<DockStartSettings> | null | undefined): DockStartSettings {
  return {
    tool_paths: {
      vina: settings?.tool_paths?.vina ?? "",
      python: settings?.tool_paths?.python ?? "",
    },
    project: {
      default_project_dir: settings?.project?.default_project_dir ?? "",
    },
  };
}

function parseSettingsResponse(rawPayload: string): SettingsResponse {
  const parsed = JSON.parse(rawPayload) as Partial<SettingsResponse>;
  return {
    ok: Boolean(parsed.ok),
    settings_path: parsed.settings_path ?? "",
    settings: parsed.settings ? normalizeSettings(parsed.settings) : null,
    error: parsed.error,
  };
}

export default function SettingsPage({ onBack }: SettingsPageProps) {
  const [settings, setSettings] = useState<DockStartSettings>(emptySettings);
  const [settingsPath, setSettingsPath] = useState("");
  const [message, setMessage] = useState("");
  const [rawError, setRawError] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  const applyResponse = useCallback((response: SettingsResponse, successMessage: string) => {
    setSettingsPath(response.settings_path);
    if (response.ok && response.settings) {
      setSettings(normalizeSettings(response.settings));
      setMessage(successMessage);
      setRawError("");
      return;
    }
    setMessage(response.error?.message ?? "设置操作失败。");
    setRawError(response.error?.raw_error ?? "");
  }, []);

  const loadSettings = useCallback(async () => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("get_settings");
      applyResponse(parseSettingsResponse(rawPayload), "已读取当前设置。");
    } catch (error) {
      setMessage("前端未能调用设置读取命令。请确认当前运行环境是 Tauri 桌面端。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  }, [applyResponse]);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const updateField = (section: "tool_paths" | "project", key: string, value: string) => {
    setSettings((current) => ({
      ...current,
      [section]: {
        ...current[section],
        [key]: value,
      },
    }));
  };

  const saveToolPath = async (toolKey: "vina" | "python", path: string, successMessage: string) => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("update_tool_path", { toolKey, path });
      applyResponse(parseSettingsResponse(rawPayload), successMessage);
    } catch (error) {
      setMessage("前端未能调用工具路径保存命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const saveAllSettings = async (nextSettings: DockStartSettings, successMessage: string) => {
    setIsBusy(true);
    try {
      const rawPayload = await invoke<string>("save_settings", {
        settingsJson: JSON.stringify(nextSettings),
      });
      applyResponse(parseSettingsResponse(rawPayload), successMessage);
    } catch (error) {
      setMessage("前端未能调用设置保存命令。");
      setRawError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsBusy(false);
    }
  };

  const clearToolPath = (toolKey: "vina" | "python", successMessage: string) => {
    void saveToolPath(toolKey, "", successMessage);
  };

  const clearProjectDir = () => {
    const nextSettings = {
      ...settings,
      project: {
        ...settings.project,
        default_project_dir: "",
      },
    };
    void saveAllSettings(nextSettings, "默认项目目录已清空。");
  };

  return (
    <PageShell labelledBy="settings-title">
      <PageHero
        eyebrow="本机路径"
        title="工具路径配置"
        titleId="settings-title"
        description="指定 DockStart 使用的 Vina、Python 和默认项目目录；留空时继续自动检测。"
        actions={
          <>
            <ActionButton variant="text" onClick={onBack}>
              返回
            </ActionButton>
            <ActionButton
              variant="primary"
              disabled={isBusy}
              onClick={() => void saveAllSettings(settings, "全部设置已保存。")}
            >
              {isBusy ? "保存中..." : "保存全部设置"}
            </ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <SectionCard
              title="本机工具与项目目录"
              description="可以逐项保存，也可以完成编辑后统一保存全部设置。"
            >
              <div className="settings-list">
                <div className="setting-row">
                  <label htmlFor="vina-path">AutoDock Vina 路径</label>
                  <PathInput
                    id="vina-path"
                    value={settings.tool_paths.vina}
                    onChange={(value) => updateField("tool_paths", "vina", value)}
                    mode="file"
                    title="选择 AutoDock Vina 可执行文件"
                    placeholder="例如 vina.exe 的完整路径，留空则从 PATH 自动检测"
                  />
                  <ActionButton
                    disabled={isBusy}
                    onClick={() => saveToolPath("vina", settings.tool_paths.vina, "AutoDock Vina 路径已保存。")}
                  >
                    保存
                  </ActionButton>
                  <ActionButton
                    variant="text"
                    disabled={isBusy}
                    onClick={() => clearToolPath("vina", "AutoDock Vina 路径已清空。")}
                  >
                    清空
                  </ActionButton>
                </div>

                <div className="setting-row">
                  <label htmlFor="python-path">Python 路径</label>
                  <PathInput
                    id="python-path"
                    value={settings.tool_paths.python}
                    onChange={(value) => updateField("tool_paths", "python", value)}
                    mode="file"
                    title="选择 Python 可执行文件"
                    placeholder="例如 python.exe 的完整路径，留空则使用当前 Python"
                  />
                  <ActionButton
                    disabled={isBusy}
                    onClick={() => saveToolPath("python", settings.tool_paths.python, "Python 路径已保存。")}
                  >
                    保存
                  </ActionButton>
                  <ActionButton
                    variant="text"
                    disabled={isBusy}
                    onClick={() => clearToolPath("python", "Python 路径已清空。")}
                  >
                    清空
                  </ActionButton>
                </div>

                <div className="setting-row">
                  <label htmlFor="default-project-dir">默认项目目录</label>
                  <PathInput
                    id="default-project-dir"
                    value={settings.project.default_project_dir}
                    onChange={(value) => updateField("project", "default_project_dir", value)}
                    mode="directory"
                    title="选择默认项目目录"
                    placeholder="可选：新建项目时默认打开的目录"
                  />
                  <ActionButton disabled={isBusy} onClick={() => saveAllSettings(settings, "默认项目目录已保存。")}>
                    保存
                  </ActionButton>
                  <ActionButton variant="text" disabled={isBusy} onClick={clearProjectDir}>
                    清空
                  </ActionButton>
                </div>
              </div>
            </SectionCard>

            {message ? <p className="settings-message" aria-live="polite">{message}</p> : null}
            {rawError ? (
              <AdvancedDetails>
                <pre>{rawError}</pre>
              </AdvancedDetails>
            ) : null}
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="配置文件">
            <p className="muted-path">{settingsPath || "尚未创建，保存后生成 dockstart_settings.json"}</p>
          </RightRailSection>

          <RightRailSection title="当前解析方式">
            <dl className="mode-context-list">
              <div>
                <dt>AutoDock Vina</dt>
                <dd>{settings.tool_paths.vina.trim() ? "使用指定路径" : "从内置工具或 PATH 检测"}</dd>
              </div>
              <div>
                <dt>Python</dt>
                <dd>{settings.tool_paths.python.trim() ? "使用指定路径" : "使用可用运行环境"}</dd>
              </div>
              <div>
                <dt>默认项目目录</dt>
                <dd>{settings.project.default_project_dir.trim() ? "已指定" : "创建项目时选择"}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="保存说明">
            <p>设置只保存在本机，不会修改系统 PATH，也不会自动安装 Vina、RDKit 或 Meeko。</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
