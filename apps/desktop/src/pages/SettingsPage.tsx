import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  ArrowSquareOut,
  CheckCircle,
  FloppyDisk,
  ShieldCheck,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import CommandResultPanel from "../components/CommandResultPanel";
import FieldHint from "../components/FieldHint";
import PathInput from "../components/PathInput";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import type {
  DockStartSettings,
  DockingDefaults,
  SettingsDiagnostics,
  SettingsDiagnosticsResponse,
  SettingsResponse,
} from "../types";
import {
  DEFAULT_DOCKING_DEFAULTS,
  DOCKING_DEFAULT_FIELDS,
  DOCKING_DEFAULT_SCORING_FUNCTIONS,
  EMPTY_DOCKING_DEFAULTS_FORM,
  EMPTY_SETTINGS,
  describeToolSource,
  dockingDefaultsEqual,
  dockingDefaultsToForm,
  formatBytes,
  parseDiagnosticsResponse,
  parseDockingDefaultsForm,
  parseSettingsResponse,
  parseToolchainSummary,
  toolStatusLabel,
  toolStatusTone,
  type DockingDefaultsForm,
  type ToolStatusEntry,
  type ToolchainSummary,
} from "../utils/settingsForm";
import { openExternalUrl } from "../utils/externalLink";
import { appVersion } from "../navigation/pages";
import {
  DOCKSTART_LICENSE,
  DOCKSTART_PROJECT_SUMMARY,
  DOCKSTART_REPOSITORY_URL,
  repositoryDisplayUrl,
} from "../utils/projectInfo";
import {
  ACCENT_IDS,
  APPEARANCE_IDS,
  THEME_MODES,
  readAppearanceState,
  resolveThemeMode,
  setAccentPreference,
  setAppearancePreference,
  setThemePreference,
  type AccentId,
  type AppearanceId,
  type AppearanceState,
  type ResolvedTheme,
  type ThemeMode,
} from "../utils/themePreference";

type SettingsPageProps = {
  onBack: () => void;
};

type Feedback = {
  tone: "ok" | "error";
  message: string;
  suggestion: string;
  rawError: string;
};

const EMPTY_FEEDBACK: Feedback = { tone: "ok", message: "", suggestion: "", rawError: "" };

const SCORING_LABELS: Record<string, string> = {
  vina: "Vina（默认）",
  vinardo: "Vinardo（偏好疏水匹配）",
};

/**
 * Appearance options. The ids map 1:1 to the CSS layers in styles/tokens.css;
 * `default` intentionally has no layer because it *is* the original palette.
 *
 * Each map is keyed by the id union, so adding a new id in
 * utils/themePreference.ts fails the type check until it is described here —
 * the UI can never offer a combination the stylesheet does not implement.
 */
const THEME_MODE_LABELS: Record<ThemeMode, { label: string; note: string }> = {
  system: { label: "跟随系统", note: "跟随 Windows 的浅色/深色设置自动切换。" },
  light: { label: "浅色", note: "始终使用浅色界面。" },
  dark: { label: "深色", note: "始终使用深色界面。" },
};

const APPEARANCE_LABELS: Record<AppearanceId, { label: string; note: string }> = {
  default: { label: "DockStart 默认", note: "当前的深蓝工作台配色。" },
  soft: { label: "柔和", note: "降低明暗反差、边框更轻，长时间阅读更轻松。" },
  contrast: { label: "高对比", note: "加深文字与边框、压深背景，弱光和投影下更清晰。" },
};

const ACCENT_LABELS: Record<AccentId, { label: string; note: string }> = {
  default: { label: "DockStart 蓝", note: "默认强调色。" },
  cyan: { label: "青蓝", note: "偏冷的青蓝色强调。" },
  graphite: { label: "石墨", note: "中性灰蓝，最克制的强调色。" },
};

const THEME_MODE_OPTIONS = THEME_MODES.map((id) => ({ id, ...THEME_MODE_LABELS[id] }));
const APPEARANCE_OPTIONS = APPEARANCE_IDS.map((id) => ({ id, ...APPEARANCE_LABELS[id] }));
const ACCENT_OPTIONS = ACCENT_IDS.map((id) => ({ id, ...ACCENT_LABELS[id] }));

function themeModeLabel(mode: ThemeMode, resolved: ResolvedTheme): string {
  if (mode === "system") return `跟随系统（当前${resolved === "dark" ? "深色" : "浅色"}）`;
  return THEME_MODE_LABELS[mode].label;
}

function optionLabel<T extends string>(options: ReadonlyArray<{ id: T; label: string }>, id: T): string {
  return options.find((option) => option.id === id)?.label ?? id;
}

/**
 * A miniature of the real palette. The swatch carries the same
 * `data-theme` / `data-appearance` / `data-accent` attributes as <html>, so it
 * resolves through the exact same CSS layers — the preview cannot drift from
 * the real theme because it *is* the real theme, scoped to a 3-block box.
 */
function AppearanceSwatch({
  appearance,
  accent,
  theme,
}: {
  appearance: AppearanceId;
  accent: AccentId;
  theme: ResolvedTheme;
}) {
  return (
    <span
      aria-hidden="true"
      className="settings-choice-swatch"
      data-accent={accent}
      data-appearance={appearance}
      data-theme={theme}
    >
      <span className="settings-choice-swatch-nav" />
      <span className="settings-choice-swatch-panel" />
      <span className="settings-choice-swatch-accent" />
    </span>
  );
}

type LinkFeedback = { tone: "ok" | "error"; message: string; suggestion: string; rawError: string };

function toolEntryTone(entry: ToolStatusEntry | null): "ok" | "warning" | "error" {
  return toolStatusTone(entry?.status ?? "");
}

function ToolEntryIcon({ tone }: { tone: "ok" | "warning" | "error" }) {
  if (tone === "ok") return <CheckCircle aria-hidden="true" size={18} weight="fill" className="settings-tool-icon is-ok" />;
  if (tone === "warning") {
    return <WarningCircle aria-hidden="true" size={18} weight="fill" className="settings-tool-icon is-warning" />;
  }
  return <XCircle aria-hidden="true" size={18} weight="fill" className="settings-tool-icon is-error" />;
}

export default function SettingsPage({ onBack }: SettingsPageProps) {
  const [settings, setSettings] = useState<DockStartSettings>(EMPTY_SETTINGS);
  const [dockingForm, setDockingForm] = useState<DockingDefaultsForm>(EMPTY_DOCKING_DEFAULTS_FORM);
  const [settingsPath, setSettingsPath] = useState("");
  const [toolchain, setToolchain] = useState<ToolchainSummary | null>(null);
  const [diagnostics, setDiagnostics] = useState<SettingsDiagnostics | null>(null);
  const [appearanceState, setAppearanceState] = useState<AppearanceState>(readAppearanceState);
  const [linkFeedback, setLinkFeedback] = useState<LinkFeedback | null>(null);
  const [isOpeningRepository, setIsOpeningRepository] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(EMPTY_FEEDBACK);
  const [isSaving, setIsSaving] = useState(false);
  const [isDetecting, setIsDetecting] = useState(false);
  const [isDiagnosing, setIsDiagnosing] = useState(false);

  const applyResponse = useCallback((response: SettingsResponse, successMessage: string) => {
    setSettingsPath(response.settings_path);
    if (response.ok && response.settings) {
      setSettings(response.settings);
      setDockingForm(dockingDefaultsToForm(response.settings.docking_defaults));
      setFeedback({ tone: "ok", message: successMessage, suggestion: "", rawError: "" });
      return;
    }
    setFeedback({
      tone: "error",
      message: response.error?.message ?? "设置操作失败。",
      suggestion: "",
      rawError: response.error?.raw_error ?? "",
    });
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const rawPayload = await invoke<string>("get_settings");
      applyResponse(parseSettingsResponse(rawPayload), "");
    } catch (error) {
      setFeedback({
        tone: "error",
        message: "无法读取本机设置。",
        suggestion: "请重新打开 DockStart 后再试；若仍然失败，可在下方环境诊断中查看原因。",
        rawError: error instanceof Error ? error.message : String(error),
      });
    }
  }, [applyResponse]);

  const detectTools = useCallback(async (force: boolean) => {
    setIsDetecting(true);
    try {
      if (force) await invoke<string>("refresh_runtime_cache");
      const rawPayload = await invoke<string>("get_toolchain_status");
      setToolchain(parseToolchainSummary(rawPayload));
    } catch (error) {
      setFeedback({
        tone: "error",
        message: "工具检测失败。",
        suggestion: "可以稍后重试；工具检测只读取本机可执行文件，不会修改任何设置。",
        rawError: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setIsDetecting(false);
    }
  }, []);

  const diagnoseStorage = useCallback(async () => {
    setIsDiagnosing(true);
    try {
      const rawPayload = await invoke<string>("diagnose_settings");
      const response: SettingsDiagnosticsResponse = parseDiagnosticsResponse(rawPayload);
      setDiagnostics(response.diagnostics);
      setSettingsPath(response.settings_path || settingsPath);
      if (response.ok && response.diagnostics) {
        const { readable, writable } = response.diagnostics;
        if (!readable) {
          setFeedback({
            tone: "error",
            message: response.diagnostics.load.message || "设置文件无法读取。",
            suggestion: response.diagnostics.load.suggestion,
            rawError: response.diagnostics.load.raw_error,
          });
        } else if (!writable) {
          setFeedback({
            tone: "error",
            message: response.diagnostics.write_probe.message || "设置文件无法写入。",
            suggestion: response.diagnostics.write_probe.suggestion,
            rawError: response.diagnostics.write_probe.raw_error,
          });
        } else {
          setFeedback({ tone: "ok", message: "设置文件读写正常。", suggestion: "", rawError: "" });
        }
        return;
      }
      setFeedback({
        tone: "error",
        message: response.error?.message ?? "设置诊断失败。",
        suggestion: "",
        rawError: response.error?.raw_error ?? "",
      });
    } catch (error) {
      setFeedback({
        tone: "error",
        message: "无法运行设置诊断。",
        suggestion: "请重新打开 DockStart 后再试。",
        rawError: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setIsDiagnosing(false);
    }
  }, [settingsPath]);

  useEffect(() => {
    void loadSettings();
    void detectTools(false);
  }, [loadSettings, detectTools]);

  const saveSettingsObject = useCallback(
    async (nextSettings: DockStartSettings, successMessage: string, refreshTools = false) => {
      setIsSaving(true);
      try {
        const rawPayload = await invoke<string>("save_settings", {
          settingsJson: JSON.stringify(nextSettings),
        });
        applyResponse(parseSettingsResponse(rawPayload), successMessage);
        if (refreshTools) void detectTools(true);
      } catch (error) {
        setFeedback({
          tone: "error",
          message: "无法保存当前设置。",
          suggestion: "请检查设置文件是否可写，可在下方环境诊断中确认。",
          rawError: error instanceof Error ? error.message : String(error),
        });
      } finally {
        setIsSaving(false);
      }
    },
    [applyResponse, detectTools],
  );

  const saveToolPath = useCallback(
    async (toolKey: "vina" | "python" | "autogrid4", path: string, successMessage: string) => {
      setIsSaving(true);
      try {
        const rawPayload = await invoke<string>("update_tool_path", { toolKey, path });
        applyResponse(parseSettingsResponse(rawPayload), successMessage);
        void detectTools(true);
      } catch (error) {
        setFeedback({
          tone: "error",
          message: "无法保存工具路径。",
          suggestion: "请确认路径可访问，并检查设置文件是否可写。",
          rawError: error instanceof Error ? error.message : String(error),
        });
      } finally {
        setIsSaving(false);
      }
    },
    [applyResponse, detectTools],
  );

  const updateToolPathField = (toolKey: "vina" | "python" | "autogrid4", value: string) => {
    setSettings((current) => ({
      ...current,
      tool_paths: { ...current.tool_paths, [toolKey]: value },
    }));
  };

  const updateProjectDir = (value: string) => {
    setSettings((current) => ({ ...current, project: { ...current.project, default_project_dir: value } }));
  };

  const updateDockingField = (key: keyof DockingDefaultsForm, value: string) => {
    setDockingForm((current) => ({ ...current, [key]: value }));
  };

  const dockDefaultsDirty = useMemo(() => {
    const parsed = parseDockingDefaultsForm(dockingForm);
    if (!parsed.ok) return true;
    return !dockingDefaultsEqual(parsed.defaults, settings.docking_defaults);
  }, [dockingForm, settings.docking_defaults]);

  const saveDockingDefaults = async () => {
    const parsed = parseDockingDefaultsForm(dockingForm);
    if (!parsed.ok) {
      setFeedback({
        tone: "error",
        message: parsed.message,
        suggestion: parsed.suggestion,
        rawError: parsed.rawError,
      });
      return;
    }
    const nextSettings: DockStartSettings = { ...settings, docking_defaults: parsed.defaults };
    await saveSettingsObject(nextSettings, "默认对接参数已保存，对新建项目生效。");
  };

  const resetDockingDefaults = () => {
    setDockingForm(dockingDefaultsToForm(DEFAULT_DOCKING_DEFAULTS));
  };

  const applyThemeMode = (next: ThemeMode) => {
    setThemePreference(next);
    setAppearanceState(readAppearanceState());
  };

  const applyAppearance = (next: AppearanceId) => {
    setAppearancePreference(next);
    setAppearanceState(readAppearanceState());
  };

  const applyAccent = (next: AccentId) => {
    setAccentPreference(next);
    setAppearanceState(readAppearanceState());
  };

  const openRepository = async () => {
    setIsOpeningRepository(true);
    try {
      const result = await openExternalUrl(DOCKSTART_REPOSITORY_URL);
      setLinkFeedback(
        result.ok
          ? { tone: "ok", message: "已在系统默认浏览器中打开 DockStart 官方仓库。", suggestion: "", rawError: "" }
          : { tone: "error", message: result.message, suggestion: result.suggestion, rawError: result.rawError },
      );
    } finally {
      setIsOpeningRepository(false);
    }
  };

  const python = toolchain?.python ?? null;
  const vina = toolchain?.vina ?? null;
  const hasDiagnostics = Boolean(diagnostics);
  const themeMode = appearanceState.mode;
  const appearanceId = appearanceState.appearance;
  const accentId = appearanceState.accent;
  const resolvedTheme = resolveThemeMode(themeMode);

  return (
    <PageShell labelledBy="settings-title">
      <PageHero
        eyebrow="本机配置"
        title="设置"
        titleId="settings-title"
        description="查看当前运行环境、指定外部工具路径、设置新建项目的默认对接参数，并检查配置文件是否正常。"
        actions={
          <>
            <ActionButton variant="text" onClick={onBack}>
              返回
            </ActionButton>
            <ActionButton
              variant="primary"
              disabled={isSaving}
              onClick={() => void saveSettingsObject(settings, "全部设置已保存。")}
            >
              {isSaving ? "保存中…" : "保存全部设置"}
            </ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content settings-panel">
            {feedback.message ? (
              feedback.tone === "ok" ? (
                <p className="settings-message" aria-live="polite">
                  {feedback.message}
                </p>
              ) : (
                <CommandResultPanel
                  title="设置操作未完成"
                  message={feedback.message}
                  suggestion={feedback.suggestion}
                  rawError={feedback.rawError}
                />
              )
            ) : null}

            <SectionCard title="常规" description="界面外观与新建项目时的默认位置。">
              <div className="settings-list">
                <div className="setting-row is-stacked">
                  <div className="setting-label-block">
                    <span className="setting-label">
                      主题
                      <FieldHint
                        label="决定 DockStart 使用深色还是浅色界面。选择「跟随系统」后，会随 Windows 的浅色/深色设置自动切换。"
                        subject="主题"
                      />
                    </span>
                    <span className="setting-current">当前：{themeModeLabel(themeMode, resolvedTheme)}</span>
                  </div>
                  <div className="settings-segmented" role="group" aria-label="主题模式">
                    {THEME_MODE_OPTIONS.map((option) => (
                      <button
                        aria-pressed={themeMode === option.id}
                        className={`settings-segmented-button${themeMode === option.id ? " is-active" : ""}`}
                        key={option.id}
                        onClick={() => applyThemeMode(option.id)}
                        title={option.note}
                        type="button"
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                  <span className="settings-row-note">
                    与本机偏好一起保存，重新启动 DockStart 后仍然生效。
                  </span>
                </div>

                <div className="setting-row is-stacked">
                  <div className="setting-label-block">
                    <span className="setting-label">
                      外观
                      <FieldHint
                        label="在不改变 DockStart 视觉语言的前提下调整明暗反差与面板层级。所有页面共用同一套配色变量，因此整个软件会一起变化。"
                        subject="外观"
                      />
                    </span>
                    <span className="setting-current">当前：{optionLabel(APPEARANCE_OPTIONS, appearanceId)}</span>
                  </div>
                  <div className="settings-choice-grid" role="group" aria-label="外观">
                    {APPEARANCE_OPTIONS.map((option) => (
                      <button
                        aria-pressed={appearanceId === option.id}
                        className={`settings-choice${appearanceId === option.id ? " is-active" : ""}`}
                        key={option.id}
                        onClick={() => applyAppearance(option.id)}
                        type="button"
                      >
                        <AppearanceSwatch appearance={option.id} accent={accentId} theme={resolvedTheme} />
                        <span className="settings-choice-label">{option.label}</span>
                        <span className="settings-choice-note">{option.note}</span>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="setting-row is-stacked">
                  <div className="setting-label-block">
                    <span className="setting-label">
                      强调色
                      <FieldHint
                        label="只调整按钮、选中态与键盘焦点等强调用的颜色。成功、警告、错误、分子与 Vina 等语义色不会改变，避免影响科学信息的可读性。"
                        subject="强调色"
                      />
                    </span>
                    <span className="setting-current">当前：{optionLabel(ACCENT_OPTIONS, accentId)}</span>
                  </div>
                  <div className="settings-choice-grid is-accent" role="group" aria-label="强调色">
                    {ACCENT_OPTIONS.map((option) => (
                      <button
                        aria-pressed={accentId === option.id}
                        className={`settings-choice${accentId === option.id ? " is-active" : ""}`}
                        key={option.id}
                        onClick={() => applyAccent(option.id)}
                        type="button"
                      >
                        <AppearanceSwatch appearance={appearanceId} accent={option.id} theme={resolvedTheme} />
                        <span className="settings-choice-label">{option.label}</span>
                        <span className="settings-choice-note">{option.note}</span>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="setting-row is-stacked">
                  <div className="setting-label-block">
                    <span className="setting-label">
                      默认项目目录
                      <FieldHint
                        label="新建项目时默认打开的父目录。留空时每次创建项目都手动选择。"
                        subject="默认项目目录"
                      />
                    </span>
                    <span className="setting-current">
                      {settings.project.default_project_dir.trim() ? "已指定" : "未指定，创建时手动选择"}
                    </span>
                  </div>
                  <div className="settings-inline-control">
                    <PathInput
                      id="default-project-dir"
                      ariaLabel="默认项目目录"
                      mode="directory"
                      onChange={updateProjectDir}
                      placeholder="可选：例如 D:\DockStartProjects"
                      title="选择默认项目目录"
                      value={settings.project.default_project_dir}
                    />
                    <ActionButton
                      disabled={isSaving}
                      onClick={() => void saveSettingsObject(settings, "默认项目目录已保存。")}
                    >
                      保存
                    </ActionButton>
                    <ActionButton
                      variant="text"
                      disabled={isSaving}
                      onClick={() =>
                        void saveSettingsObject(
                          { ...settings, project: { ...settings.project, default_project_dir: "" } },
                          "默认项目目录已清空。",
                        )
                      }
                    >
                      清空
                    </ActionButton>
                  </div>
                </div>
              </div>
            </SectionCard>

            <SectionCard
              title="工具与运行环境"
              description="DockStart 实际使用的 Python 与 AutoDock Vina。留空时继续自动检测。"
            >
              <div className="settings-tool-list">
                <div className="settings-tool-card">
                  <div className="settings-tool-head">
                    <ToolEntryIcon tone={toolEntryTone(python)} />
                    <div className="settings-tool-title">
                      <span className="setting-label">
                        Python 解释器
                        <FieldHint
                          label="DockStart 后端（结构准备、PDBQT 转换等）使用的 Python 3 解释器。留空时按内置版本、系统环境依次自动检测，不会使用 MGLTools 自带的 Python 2.7。"
                          subject="Python 解释器"
                        />
                      </span>
                      <span className="settings-tool-meta">
                        {describeToolSource(python?.source ?? "", settings.tool_paths.python)}
                        {python?.version ? ` · Python ${python.version}` : ""}
                      </span>
                    </div>
                    <StatusBadge tone={toolStatusTone(python?.status ?? "")}>
                      {toolStatusLabel(python?.status ?? "")}
                    </StatusBadge>
                  </div>
                  {python?.path ? <p className="muted-path">{python.path}</p> : null}
                  {python?.message && toolStatusTone(python.status) !== "ok" ? (
                    <p className="settings-tool-message">{python.message}</p>
                  ) : null}
                  <div className="settings-inline-control">
                    <PathInput
                      id="python-path"
                      ariaLabel="Python 可执行文件路径"
                      mode="file"
                      onChange={(value) => updateToolPathField("python", value)}
                      placeholder="留空表示自动检测"
                      title="选择 Python 可执行文件"
                      value={settings.tool_paths.python}
                    />
                    <ActionButton
                      disabled={isSaving}
                      onClick={() => void saveToolPath("python", settings.tool_paths.python, "Python 路径已保存。")}
                    >
                      保存
                    </ActionButton>
                    <ActionButton
                      variant="text"
                      disabled={isSaving || !settings.tool_paths.python.trim()}
                      onClick={() => void saveToolPath("python", "", "Python 路径已清空，恢复自动检测。")}
                    >
                      清空
                    </ActionButton>
                  </div>
                </div>

                <div className="settings-tool-card">
                  <div className="settings-tool-head">
                    <ToolEntryIcon tone={toolEntryTone(vina)} />
                    <div className="settings-tool-title">
                      <span className="setting-label">
                        AutoDock Vina
                        <FieldHint
                          label="执行分子对接的可执行文件。DockStart 需要 AutoDock Vina 1.2.x；留空时优先使用随应用附带的内置版本，其次才使用系统 PATH 中的 vina。"
                          subject="AutoDock Vina"
                        />
                      </span>
                      <span className="settings-tool-meta">
                        {describeToolSource(vina?.source ?? "", settings.tool_paths.vina)}
                        {vina?.version ? ` · Vina ${vina.version}` : ""}
                      </span>
                    </div>
                    <StatusBadge tone={toolStatusTone(vina?.status ?? "")}>
                      {toolStatusLabel(vina?.status ?? "")}
                    </StatusBadge>
                  </div>
                  {vina?.path ? <p className="muted-path">{vina.path}</p> : null}
                  {vina?.message && toolStatusTone(vina.status) !== "ok" ? (
                    <p className="settings-tool-message">{vina.message}</p>
                  ) : null}
                  <div className="settings-inline-control">
                    <PathInput
                      id="vina-path"
                      ariaLabel="AutoDock Vina 可执行文件路径"
                      mode="file"
                      onChange={(value) => updateToolPathField("vina", value)}
                      placeholder="留空表示使用内置版本或自动检测"
                      title="选择 AutoDock Vina 可执行文件"
                      value={settings.tool_paths.vina}
                    />
                    <ActionButton
                      disabled={isSaving}
                      onClick={() => void saveToolPath("vina", settings.tool_paths.vina, "AutoDock Vina 路径已保存。")}
                    >
                      保存
                    </ActionButton>
                    <ActionButton
                      variant="text"
                      disabled={isSaving || !settings.tool_paths.vina.trim()}
                      onClick={() => void saveToolPath("vina", "", "AutoDock Vina 路径已清空，恢复自动检测。")}
                    >
                      清空
                    </ActionButton>
                  </div>
                </div>
              </div>

              <div className="settings-section-actions">
                <ActionButton disabled={isDetecting} onClick={() => void detectTools(true)}>
                  {isDetecting ? "检测中…" : "重新检测"}
                </ActionButton>
                <span className="settings-row-note">
                  {toolchain?.runtimeMode ? `当前运行模式：${toolchain.runtimeMode}` : "检测会实际运行工具以读取版本。"}
                </span>
              </div>
            </SectionCard>

            <SectionCard
              title="默认对接参数"
              description="只影响之后新建的项目；已经存在的项目保留自己的参数，不会被覆盖。"
            >
              <div className="docking-defaults-grid">
                <label className="docking-default-field">
                  <span>
                    评分函数
                    <FieldHint
                      label="对接打分使用的评分函数。Vina 是标准评分函数；Vinardo 对疏水匹配更敏感。AD4 maps 需要 AutoGrid4 亲和力图，只能在批量筛选流程中单独选择，不能设为默认评分函数。"
                      subject="评分函数"
                    />
                  </span>
                  <select
                    aria-label="评分函数"
                    disabled={isSaving}
                    onChange={(event) => updateDockingField("scoring", event.target.value)}
                    value={dockingForm.scoring}
                  >
                    {DOCKING_DEFAULT_SCORING_FUNCTIONS.map((value) => (
                      <option key={value} value={value}>
                        {SCORING_LABELS[value] ?? value}
                      </option>
                    ))}
                  </select>
                </label>

                {DOCKING_DEFAULT_FIELDS.map((field) => (
                  <label className="docking-default-field" key={field.key}>
                    <span>
                      {field.label}
                      <FieldHint label={field.explain} subject={field.label} />
                    </span>
                    <input
                      aria-label={field.label}
                      disabled={isSaving}
                      inputMode={field.integer ? "numeric" : "decimal"}
                      onChange={(event) => updateDockingField(field.key, event.target.value)}
                      placeholder={field.optional ? "留空" : `${field.min} - ${field.max}`}
                      value={dockingForm[field.key]}
                    />
                    <small>
                      {field.hint} · 范围 {field.min} - {field.max}
                    </small>
                  </label>
                ))}
              </div>

              <div className="settings-section-actions">
                <ActionButton variant="primary" disabled={isSaving || !dockDefaultsDirty} onClick={() => void saveDockingDefaults()}>
                  <FloppyDisk aria-hidden="true" size={16} />
                  保存默认参数
                </ActionButton>
                <ActionButton variant="text" disabled={isSaving} onClick={resetDockingDefaults}>
                  恢复推荐值
                </ActionButton>
                <span className="settings-row-note">
                  {dockDefaultsDirty ? "有未保存的修改。" : "当前值与已保存的默认值一致。"}
                </span>
              </div>
            </SectionCard>

            <SectionCard
              title="环境诊断"
              description="实际检查当前环境。这里的状态来自真实检测，不是预设结果。"
            >
              <div className="settings-diagnostics">
                <div className="settings-diagnostic-row">
                  <span className="settings-diagnostic-label">Python</span>
                  <StatusBadge tone={toolEntryTone(python)}>{toolStatusLabel(python?.status ?? "")}</StatusBadge>
                  <span className="settings-diagnostic-value">
                    {python?.version ? `Python ${python.version}` : "未检测到版本"}
                  </span>
                </div>
                <div className="settings-diagnostic-row">
                  <span className="settings-diagnostic-label">AutoDock Vina</span>
                  <StatusBadge tone={toolEntryTone(vina)}>{toolStatusLabel(vina?.status ?? "")}</StatusBadge>
                  <span className="settings-diagnostic-value">
                    {vina?.version ? `Vina ${vina.version}` : "未检测到版本"}
                  </span>
                </div>
                <div className="settings-diagnostic-row">
                  <span className="settings-diagnostic-label">设置文件</span>
                  <StatusBadge tone={!hasDiagnostics ? "muted" : diagnostics?.writable ? "ok" : "error"}>
                    {!hasDiagnostics ? "未检查" : diagnostics?.writable ? "可读写" : "不可写"}
                  </StatusBadge>
                  <span className="settings-diagnostic-value">
                    {hasDiagnostics && diagnostics?.file_exists
                      ? `已存在 · ${formatBytes(diagnostics.file_size_bytes)}`
                      : "尚未创建，保存后生成"}
                  </span>
                </div>
                {settingsPath ? <p className="muted-path settings-diagnostic-path">{settingsPath}</p> : null}
                {hasDiagnostics && diagnostics?.file_modified_at ? (
                  <p className="settings-row-note">最后修改：{diagnostics.file_modified_at}</p>
                ) : null}
                {python?.rawError || vina?.rawError ? (
                  <AdvancedDetails summary="工具检测原始信息">
                    <pre>{[python?.rawError, vina?.rawError].filter(Boolean).join("\n")}</pre>
                  </AdvancedDetails>
                ) : null}
              </div>

              <div className="settings-section-actions">
                <ActionButton disabled={isDiagnosing || isDetecting} onClick={() => void diagnoseStorage()}>
                  <ShieldCheck aria-hidden="true" size={16} />
                  {isDiagnosing ? "检查中…" : "检查设置文件"}
                </ActionButton>
                <ActionButton disabled={isDetecting || isDiagnosing} onClick={() => void detectTools(true)}>
                  {isDetecting ? "检测中…" : "测试 Python 与 Vina"}
                </ActionButton>
              </div>
            </SectionCard>

            <SectionCard title="关于项目" description="DockStart 是什么，以及在哪里获取源码与反馈问题。">
              <div className="settings-about">
                <p className="settings-about-summary">{DOCKSTART_PROJECT_SUMMARY}</p>

                <div className="settings-about-repository">
                  <ActionButton
                    className="settings-repository-button"
                    disabled={isOpeningRepository}
                    onClick={() => void openRepository()}
                  >
                    <ArrowSquareOut aria-hidden="true" size={16} />
                    {isOpeningRepository ? "正在打开…" : "打开 GitHub 仓库"}
                  </ActionButton>
                  <span className="settings-about-url">
                    {repositoryDisplayUrl()}
                    <span className="settings-about-hint">使用系统默认浏览器打开外部网站</span>
                  </span>
                </div>

                {linkFeedback ? (
                  linkFeedback.tone === "ok" ? (
                    <p aria-live="polite" className="settings-about-result is-ok">
                      {linkFeedback.message}
                    </p>
                  ) : (
                    <div className="settings-about-result is-error" role="alert">
                      <p>{linkFeedback.message}</p>
                      {linkFeedback.suggestion ? <p>{linkFeedback.suggestion}</p> : null}
                      {linkFeedback.rawError ? (
                        <AdvancedDetails summary="技术信息（用于排查）">
                          <pre>{linkFeedback.rawError}</pre>
                        </AdvancedDetails>
                      ) : null}
                    </div>
                  )
                ) : null}

                <dl className="settings-about-meta">
                  <div>
                    <dt>版本</dt>
                    <dd>DockStart v{appVersion}</dd>
                  </div>
                  <div>
                    <dt>许可证</dt>
                    <dd>{DOCKSTART_LICENSE}</dd>
                  </div>
                  <div>
                    <dt>运行方式</dt>
                    <dd>完全在本机运行，不上传数据</dd>
                  </div>
                </dl>
              </div>
            </SectionCard>

            <SectionCard title="高级设置" description="只在需要时调整。默认收起，避免影响正常使用。">
              <AdvancedDetails summary="展开高级设置">
                <div className="settings-list">
                  <div className="setting-row is-stacked">
                    <div className="setting-label-block">
                      <span className="setting-label">
                        AutoGrid4 路径
                        <FieldHint
                          label="外部 AutoGrid4 可执行文件，仅在 AutoDock4 maps 协议下需要。AutoGrid4 是 GPL 外部工具，DockStart 不随安装包分发。"
                          subject="AutoGrid4"
                        />
                      </span>
                      <span className="setting-current">
                        {settings.tool_paths.autogrid4.trim() ? "已指定外部工具" : "未指定，AD4 流程需要时再配置"}
                      </span>
                    </div>
                    <div className="settings-inline-control">
                      <PathInput
                        id="autogrid4-path"
                        ariaLabel="AutoGrid4 可执行文件路径"
                        mode="file"
                        onChange={(value) => updateToolPathField("autogrid4", value)}
                        placeholder="留空表示从系统 PATH 检测"
                        title="选择外部 AutoGrid4 可执行文件"
                        value={settings.tool_paths.autogrid4}
                      />
                      <ActionButton
                        disabled={isSaving}
                        onClick={() => void saveToolPath("autogrid4", settings.tool_paths.autogrid4, "AutoGrid4 路径已保存。")}
                      >
                        保存
                      </ActionButton>
                      <ActionButton
                        variant="text"
                        disabled={isSaving || !settings.tool_paths.autogrid4.trim()}
                        onClick={() => void saveToolPath("autogrid4", "", "AutoGrid4 路径已清空。")}
                      >
                        清空
                      </ActionButton>
                    </div>
                  </div>

                  <div className="setting-row is-stacked">
                    <div className="setting-label-block">
                      <span className="setting-label">设置文件</span>
                      <span className="setting-current">所有设置都保存在这个 JSON 文件中。</span>
                    </div>
                    <p className="muted-path">{settingsPath || "尚未创建，保存后生成 dockstart_settings.json"}</p>
                    {diagnostics?.env_override ? (
                      <p className="settings-row-note">
                        当前由环境变量 {`DOCKSTART_SETTINGS_PATH`} 指定位置：{diagnostics.env_override}
                      </p>
                    ) : null}
                  </div>

                  <div className="setting-row is-stacked">
                    <div className="setting-label-block">
                      <span className="setting-label">当前配置内容</span>
                      <span className="setting-current">只读预览，手动编辑文件后请重新打开本页。</span>
                    </div>
                    <AdvancedDetails summary="查看 JSON">
                      <pre>{JSON.stringify(settings, null, 2)}</pre>
                    </AdvancedDetails>
                  </div>
                </div>
              </AdvancedDetails>
            </SectionCard>
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="当前生效">
            <dl className="mode-context-list">
              <div>
                <dt>Python</dt>
                <dd>
                  {describeToolSource(python?.source ?? "", settings.tool_paths.python)}
                  {python?.version ? `（${python.version}）` : ""}
                </dd>
              </div>
              <div>
                <dt>AutoDock Vina</dt>
                <dd>
                  {describeToolSource(vina?.source ?? "", settings.tool_paths.vina)}
                  {vina?.version ? `（${vina.version}）` : ""}
                </dd>
              </div>
              <div>
                <dt>默认项目目录</dt>
                <dd>{settings.project.default_project_dir.trim() || "创建项目时选择"}</dd>
              </div>
              <div>
                <dt>主题</dt>
                <dd>{themeModeLabel(themeMode, resolvedTheme)}</dd>
              </div>
              <div>
                <dt>外观与强调色</dt>
                <dd>
                  {optionLabel(APPEARANCE_OPTIONS, appearanceId)} · {optionLabel(ACCENT_OPTIONS, accentId)}
                </dd>
              </div>
              <div>
                <dt>默认搜索彻底程度</dt>
                <dd>{settings.docking_defaults.exhaustiveness}</dd>
              </div>
              <div>
                <dt>默认输出构象数</dt>
                <dd>{settings.docking_defaults.num_modes}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="设置保存在哪里">
            <p className="muted-path">{settingsPath || "尚未创建，保存后生成 dockstart_settings.json"}</p>
            <p>设置只保存在本机，不会修改系统 PATH，也不会改动已有项目的参数。</p>
          </RightRailSection>

          <RightRailSection title="这些参数影响什么">
            <p>默认对接参数只用于新建项目。项目一旦创建，就会保留自己的数值，修改这里的默认值不会改动它。</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
