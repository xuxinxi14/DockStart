import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
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
import { readThemePreference, setThemePreference, type ThemeMode } from "../utils/themePreference";

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
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => readThemePreference());
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

  const applyTheme = (next: ThemeMode) => {
    setThemeMode(next);
    setThemePreference(next);
  };

  const python = toolchain?.python ?? null;
  const vina = toolchain?.vina ?? null;
  const hasDiagnostics = Boolean(diagnostics);

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
                    <span className="setting-label">主题</span>
                    <span className="setting-current">
                      当前：{themeMode === "dark" ? "深色" : "浅色"}
                    </span>
                  </div>
                  <div className="settings-segmented" role="group" aria-label="主题">
                    <button
                      aria-pressed={themeMode === "dark"}
                      className={`settings-segmented-button${themeMode === "dark" ? " is-active" : ""}`}
                      onClick={() => applyTheme("dark")}
                      type="button"
                    >
                      深色
                    </button>
                    <button
                      aria-pressed={themeMode === "light"}
                      className={`settings-segmented-button${themeMode === "light" ? " is-active" : ""}`}
                      onClick={() => applyTheme("light")}
                      type="button"
                    >
                      浅色
                    </button>
                  </div>
                  <span className="settings-row-note">与本机偏好一起保存，下次打开仍然生效。</span>
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
