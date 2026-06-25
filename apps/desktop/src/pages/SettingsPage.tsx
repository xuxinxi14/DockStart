import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import PathInput from "../components/PathInput";
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
    <section className="settings-page" aria-labelledby="settings-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回
      </button>

      <div className="page-heading">
        <p className="eyebrow">SettingsPage</p>
        <h1 id="settings-title">工具路径配置</h1>
        <p>
          这里仅保存本机工具路径。DockStart 会优先使用这些路径检测 Vina 和
          Python；留空时继续走自动检测或当前环境。
        </p>
      </div>

      <div className="settings-meta">
        <span>配置文件</span>
        <code>{settingsPath || "尚未创建，保存后生成 dockstart_settings.json"}</code>
      </div>

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
          <button
            className="secondary-button"
            type="button"
            disabled={isBusy}
            onClick={() => saveToolPath("vina", settings.tool_paths.vina, "AutoDock Vina 路径已保存。")}
          >
            保存
          </button>
          <button
            className="text-button inline"
            type="button"
            disabled={isBusy}
            onClick={() => clearToolPath("vina", "AutoDock Vina 路径已清空。")}
          >
            清空
          </button>
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
          <button
            className="secondary-button"
            type="button"
            disabled={isBusy}
            onClick={() => saveToolPath("python", settings.tool_paths.python, "Python 路径已保存。")}
          >
            保存
          </button>
          <button
            className="text-button inline"
            type="button"
            disabled={isBusy}
            onClick={() => clearToolPath("python", "Python 路径已清空。")}
          >
            清空
          </button>
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
          <button
            className="secondary-button"
            type="button"
            disabled={isBusy}
            onClick={() => saveAllSettings(settings, "默认项目目录已保存。")}
          >
            保存
          </button>
          <button className="text-button inline" type="button" disabled={isBusy} onClick={clearProjectDir}>
            清空
          </button>
        </div>
      </div>

      {message ? <p className="settings-message">{message}</p> : null}
      {rawError ? (
        <details className="raw-error">
          <summary>查看 raw_error</summary>
          <pre>{rawError}</pre>
        </details>
      ) : null}
    </section>
  );
}
