import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import StatusBadge from "../components/StatusBadge";
import type {
  DiagnosticReportResponse,
  PostInstallCheckResponse,
  ToolCheckResult,
  ToolSource,
  ToolStatus,
  ToolchainRepairSuggestion,
  ToolchainRepairSuggestionsResponse,
  ToolchainStatusResponse,
} from "../types";
import OperationLoadingDialog from "../components/OperationLoadingDialog";

type ToolchainStatusPageProps = {
  onBack: () => void;
  onOpenHelp?: () => void;
  onOpenSettings?: () => void;
};

const statusText: Record<ToolStatus, string> = {
  ok: "可用",
  missing: "未配置",
  error: "检测错误",
  unknown: "状态未知",
};

const sourceText: Record<ToolSource, string> = {
  bundled: "随应用提供",
  configured: "用户配置路径",
  auto: "系统自动检测",
  current_environment: "Python 运行环境",
  frontend_dependency: "应用内置资源",
  missing: "尚未找到",
  unknown: "未知来源",
};

const packageStatusText: Record<NonNullable<ToolchainStatusResponse["bundled_python_integrity"]>["status"], string> = {
  ready: "文件校验通过",
  incomplete: "文件待补全",
  missing: "未随附",
};

const severityText: Record<string, string> = {
  error: "需要处理",
  warning: "建议处理",
  info: "提示",
};

function normalizeTool(item: Partial<ToolCheckResult> | null | undefined, fallbackKey = "tool"): ToolCheckResult | null {
  if (!item) {
    return null;
  }
  return {
    key: item.key ?? fallbackKey,
    name: item.name ?? fallbackKey,
    status: item.status ?? "unknown",
    version: item.version ?? "",
    path: item.path ?? "",
    message: item.message ?? "暂无说明。",
    raw_error: item.raw_error ?? "",
    source: item.source ?? "unknown",
    bundled_path: item.bundled_path ?? "",
    is_bundled: Boolean(item.is_bundled),
  };
}

function normalizeBinaryIntegrity(
  item: Partial<NonNullable<ToolchainStatusResponse["bundled_python_integrity"]>> | null | undefined,
): NonNullable<ToolchainStatusResponse["bundled_python_integrity"]> | null {
  if (!item) {
    return null;
  }
  return {
    status: item.status ?? "missing",
    binary_path: item.binary_path ?? "",
    binary_exists: Boolean(item.binary_exists),
    sha256: item.sha256 ?? "",
    manifest_sha256: item.manifest_sha256 ?? "",
    sha256_matches: Boolean(item.sha256_matches),
    manifest_bundled: Boolean(item.manifest_bundled),
    manifest_version: item.manifest_version ?? "",
    manifest_source: item.manifest_source ?? "",
    manifest_prepared_at: item.manifest_prepared_at ?? "",
    warnings: item.warnings ?? [],
    message: item.message ?? "",
  };
}

function normalizeVinaIntegrity(
  item: Partial<NonNullable<ToolchainStatusResponse["bundled_vina_integrity"]>> | null | undefined,
): NonNullable<ToolchainStatusResponse["bundled_vina_integrity"]> | null {
  const base = normalizeBinaryIntegrity(item);
  if (!base) {
    return null;
  }
  return {
    ...base,
    license_path: item?.license_path ?? "",
    license_exists: Boolean(item?.license_exists),
    third_party_notices_path: item?.third_party_notices_path ?? "",
    third_party_notices_exists: Boolean(item?.third_party_notices_exists),
    third_party_notices_has_autodock_vina: Boolean(item?.third_party_notices_has_autodock_vina),
  };
}

function normalizeResponse(rawPayload: string): ToolchainStatusResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ToolchainStatusResponse>;
  const vinaIntegrity = normalizeVinaIntegrity(parsed.bundled_vina_integrity);
  const pythonIntegrity = normalizeBinaryIntegrity(parsed.bundled_python_integrity);
  return {
    ok: Boolean(parsed.ok),
    runtime_mode: parsed.runtime_mode ?? "unknown",
    resource_dir: parsed.resource_dir ?? "",
    toolchain_root: parsed.toolchain_root ?? "",
    tools_dir: parsed.tools_dir ?? "",
    licenses_dir: parsed.licenses_dir ?? "",
    manifest_file: parsed.manifest_file ?? "",
    manifest_exists: Boolean(parsed.manifest_exists),
    manifest: parsed.manifest ?? {},
    manifest_error: parsed.manifest_error ?? "",
    bundled_vina: {
      exists: Boolean(parsed.bundled_vina?.exists),
      path: parsed.bundled_vina?.path ?? "",
      version: parsed.bundled_vina?.version ?? "",
      status: parsed.bundled_vina?.status ?? "unknown",
      message: parsed.bundled_vina?.message ?? "暂无说明。",
      raw_error: parsed.bundled_vina?.raw_error ?? "",
      sha256: parsed.bundled_vina?.sha256 ?? "",
      package_status: parsed.bundled_vina?.package_status ?? vinaIntegrity?.status ?? "missing",
    },
    bundled_vina_integrity: vinaIntegrity,
    bundled_vina_package: parsed.bundled_vina_package ?? null,
    bundled_python: {
      exists: Boolean(parsed.bundled_python?.exists),
      path: parsed.bundled_python?.path ?? "",
      version: parsed.bundled_python?.version ?? "",
      status: parsed.bundled_python?.status ?? "unknown",
      message: parsed.bundled_python?.message ?? "暂无说明。",
      raw_error: parsed.bundled_python?.raw_error ?? "",
      sha256: parsed.bundled_python?.sha256 ?? "",
      package_status: parsed.bundled_python?.package_status ?? pythonIntegrity?.status ?? "missing",
    },
    bundled_python_integrity: pythonIntegrity,
    bundled_python_package: parsed.bundled_python_package ?? null,
    warnings: parsed.warnings ?? [...(vinaIntegrity?.warnings ?? []), ...(pythonIntegrity?.warnings ?? [])],
    active_vina: normalizeTool(parsed.active_vina, "vina"),
    active_source: parsed.active_source ?? "unknown",
    autogrid4: normalizeTool(parsed.autogrid4, "autogrid4"),
    autogrid4_source: parsed.autogrid4_source ?? "unknown",
    resolved_python: normalizeTool(parsed.resolved_python, "python"),
    python_source: parsed.python_source ?? "unknown",
    meeko_for_python: normalizeTool(parsed.meeko_for_python, "meeko"),
    rdkit_for_python: normalizeTool(parsed.rdkit_for_python, "rdkit"),
    meeko_python_source: parsed.meeko_python_source ?? "unknown",
    rdkit_python_source: parsed.rdkit_python_source ?? "unknown",
    first_run_guidance: parsed.first_run_guidance,
    licenses: {
      exists: Boolean(parsed.licenses?.exists),
      third_party_notices: parsed.licenses?.third_party_notices ?? "",
      third_party_notices_exists: Boolean(parsed.licenses?.third_party_notices_exists),
    },
    resources: {
      exists: Boolean(parsed.resources?.exists),
      tools_dir_exists: Boolean(parsed.resources?.tools_dir_exists),
      vina_dir_exists: Boolean(parsed.resources?.vina_dir_exists),
      python_dir_exists: Boolean(parsed.resources?.python_dir_exists),
    },
    full_status: parsed.full_status ?? "missing",
    message: parsed.message ?? "",
    error: parsed.error,
  };
}

function buildRepairFromStatus(status: ToolchainStatusResponse): ToolchainRepairSuggestionsResponse {
  const suggestions: ToolchainRepairSuggestion[] = [];
  if (status.active_vina?.status !== "ok") {
    suggestions.push({
      issue: "vina_missing",
      severity: "error",
      affected_mode: "Basic Mode / Assisted Mode",
      explanation: "DockStart 没有检测到可用 AutoDock Vina，因此无法执行真实 docking。",
      recommended_fix: "准备 AutoDock Vina，并在设置页填写 vina.exe 路径；完整发行包则应先核验随附 Vina。",
      documentation_link: "docs/toolchain_repair_guide.md",
      copyable_commands: ["vina --version"],
      manual_steps: [
        "确认本机或发行包已有 AutoDock Vina。",
        "在命令行运行 vina --version 或 vina.exe --version 验证。",
        "必要时在 DockStart 设置页填写 vina.exe 的完整路径。",
        "回到工具链页点击“重新检测”。",
      ],
    });
  }

  const pythonReady = status.resolved_python?.status === "ok";
  const rdkitReady = status.rdkit_for_python?.status === "ok";
  const meekoReady = status.meeko_for_python?.status === "ok";
  if (!pythonReady || !rdkitReady || !meekoReady) {
    const missing = [
      !pythonReady ? "Python" : "",
      !rdkitReady ? "RDKit" : "",
      !meekoReady ? "Meeko" : "",
    ].filter(Boolean);
    suggestions.push({
      issue: "python_rdkit_meeko_incomplete",
      severity: "warning",
      affected_mode: "Assisted Mode",
      explanation: `自动准备 PDBQT 需要可用 Python + RDKit + Meeko；当前缺少或未检测通过：${missing.join("、")}。`,
      recommended_fix: "优先使用发行包随附工具链；开发环境可配置独立 Python 工具链。",
      documentation_link: "docs/toolchain_repair_guide.md",
      copyable_commands: [
        "python -c \"import rdkit, meeko; print('RDKit/Meeko ok')\"",
      ],
      manual_steps: [
        "先确认当前 Python 路径是否来自 DockStart 完整发行包或独立工具链。",
        "确认 rdkit 和 meeko 可以从该 Python import。",
        "必要时在 DockStart 设置页填写替代 Python 的完整路径。",
        "回到工具链页点击“重新检测”。",
      ],
    });
  }

  const pythonPath = status.resolved_python?.path.replace(/\//g, "\\").toLowerCase() ?? "";
  if (pythonPath.includes("windowsapps") || pythonPath.includes("pythonsoftwarefoundation")) {
    suggestions.push({
      issue: "microsoft_store_python_not_recommended",
      severity: "warning",
      affected_mode: "Assisted Mode",
      explanation: "当前 Python 看起来来自 Microsoft Store；该环境的包管理和路径行为不适合作为稳定的 RDKit/Meeko 工具链。",
      recommended_fix: "改用 DockStart 随附工具链或独立 Python 环境，并在设置页配置其 python.exe。",
      documentation_link: "docs/toolchain_repair_guide.md",
      copyable_commands: [],
      manual_steps: [
        "不要把 RDKit/Meeko 安装进 Microsoft Store Python。",
        "选择 DockStart 随附 Python 或创建独立工具链。",
        "把目标环境的 python.exe 填入 DockStart 设置页。",
      ],
    });
  }

  return {
    ok: status.ok,
    suggestions,
    message: suggestions.length ? "工具链修复建议已由本次检测结果生成。" : "当前没有需要修复的关键工具链问题。",
    error: status.error ?? null,
  };
}

function normalizePostInstallCheck(rawPayload: string): PostInstallCheckResponse {
  const parsed = JSON.parse(rawPayload) as Partial<PostInstallCheckResponse>;
  return {
    ok: Boolean(parsed.ok),
    generated_at: parsed.generated_at ?? "",
    app_version: parsed.app_version ?? "",
    os: {
      system: parsed.os?.system ?? "",
      release: parsed.os?.release ?? "",
      version: parsed.os?.version ?? "",
      machine: parsed.os?.machine ?? "",
    },
    runtime_mode: parsed.runtime_mode ?? "unknown",
    release_build_mode: parsed.release_build_mode ?? "unknown",
    paths: {
      settings_path: parsed.paths?.settings_path ?? "",
      resource_dir: parsed.paths?.resource_dir ?? "",
      toolchain_root: parsed.paths?.toolchain_root ?? "",
    },
    tools: parsed.tools ?? {},
    demo_projects: {
      ok: Boolean(parsed.demo_projects?.ok),
      available: Boolean(parsed.demo_projects?.available),
      count: parsed.demo_projects?.count ?? 0,
      demos: parsed.demo_projects?.demos ?? [],
    },
    modes: {
      basic_mode_available: Boolean(parsed.modes?.basic_mode_available),
      assisted_mode_available: Boolean(parsed.modes?.assisted_mode_available),
      demo_mode_available: Boolean(parsed.modes?.demo_mode_available),
      recommended_mode: parsed.modes?.recommended_mode ?? "setup",
      next_action: parsed.modes?.next_action ?? "",
    },
    issues: parsed.issues ?? [],
    privacy_note: parsed.privacy_note ?? "",
    message: parsed.message ?? "",
    error: parsed.error ?? null,
  };
}

function normalizeDiagnosticReport(rawPayload: string): DiagnosticReportResponse {
  const parsed = JSON.parse(rawPayload) as Partial<DiagnosticReportResponse>;
  return {
    ok: Boolean(parsed.ok),
    report_file: parsed.report_file ?? "",
    generated_at: parsed.generated_at ?? "",
    check: parsed.check ? normalizePostInstallCheck(JSON.stringify(parsed.check)) : normalizePostInstallCheck("{}"),
    message: parsed.message ?? "",
    error: parsed.error ?? null,
  };
}

function buildFrontendRepairError(error: unknown): ToolchainRepairSuggestionsResponse {
  const rawError = error instanceof Error ? error.message : String(error);
  return {
    ok: false,
    suggestions: [],
    message: "无法读取工具链修复建议。",
    error: {
      code: "FRONTEND_TOOLCHAIN_REPAIR_ERROR",
      message: "无法读取工具链修复建议。",
      raw_error: rawError,
      suggestion: "请重新打开工具链页；若仍失败，请查看原始诊断。",
    },
  };
}

function buildFrontendError(error: unknown): ToolchainStatusResponse {
  const rawError = error instanceof Error ? error.message : String(error);
  const frontendTool: ToolCheckResult = {
    key: "toolchain",
    name: "工具链状态",
    status: "error",
    version: "",
    path: "",
    message: "DockStart 无法读取工具链状态。",
    raw_error: rawError,
    source: "unknown",
    bundled_path: "",
    is_bundled: false,
  };

  return {
    ok: false,
    runtime_mode: "unknown",
    resource_dir: "",
    toolchain_root: "",
    tools_dir: "",
    licenses_dir: "",
    manifest_file: "",
    manifest_exists: false,
    manifest: {},
    manifest_error: "",
    bundled_vina: {
      exists: false,
      path: "",
      version: "",
      status: "error",
      message: frontendTool.message,
      raw_error: rawError,
      sha256: "",
      package_status: "missing",
    },
    bundled_vina_integrity: null,
    bundled_vina_package: null,
    bundled_python: {
      exists: false,
      path: "",
      version: "",
      status: "error",
      message: frontendTool.message,
      raw_error: rawError,
      sha256: "",
      package_status: "missing",
    },
    bundled_python_integrity: null,
    bundled_python_package: null,
    warnings: [],
    active_vina: frontendTool,
    active_source: "unknown",
    autogrid4: null,
    autogrid4_source: "unknown",
    resolved_python: null,
    python_source: "unknown",
    meeko_for_python: null,
    rdkit_for_python: null,
    meeko_python_source: "unknown",
    rdkit_python_source: "unknown",
    first_run_guidance: {
      status: "unknown",
      recommended_action: "请重新打开应用，并在工具链页重新检测。",
      primary_page: "toolchain-status",
      message: "暂时无法读取本机工具链状态。",
    },
    licenses: {
      exists: false,
      third_party_notices: "",
      third_party_notices_exists: false,
    },
    resources: {
      exists: false,
      tools_dir_exists: false,
      vina_dir_exists: false,
      python_dir_exists: false,
    },
    full_status: "missing",
    message: "读取工具链状态失败。",
    error: {
      code: "FRONTEND_TOOLCHAIN_STATUS_ERROR",
      message: frontendTool.message,
      raw_error: rawError,
      suggestion: "请重新打开应用后重试；若仍失败，请查看原始诊断。",
    },
  };
}

type StatusTone = "ok" | "warning" | "error" | "info" | "muted";

function statusTone(status: ToolStatus | undefined): StatusTone {
  if (status === "ok") {
    return "ok";
  }
  if (status === "missing") {
    return "warning";
  }
  if (status === "error") {
    return "error";
  }
  return "muted";
}

function packageStatusTone(
  status: NonNullable<ToolchainStatusResponse["bundled_python_integrity"]>["status"],
): StatusTone {
  if (status === "ready") {
    return "ok";
  }
  if (status === "incomplete") {
    return "warning";
  }
  return "error";
}

function booleanText(value: boolean): string {
  return value ? "存在" : "不存在";
}

function shortHash(value: string): string {
  return value ? `${value.slice(0, 16)}...` : "未计算";
}

function pathOrEmpty(path: string | undefined): string {
  return path && path.trim() ? path : "未获取";
}

function severityTone(severity: string): StatusTone {
  if (severity === "error") return "error";
  if (severity === "warning") return "warning";
  if (severity === "info") return "info";
  return "muted";
}

function modeAvailabilityText(value: boolean): string {
  return value ? "可用" : "不可用";
}

export default function ToolchainStatusPage({ onBack, onOpenHelp, onOpenSettings }: ToolchainStatusPageProps) {
  useLanguage();
  const [status, setStatus] = useState<ToolchainStatusResponse | null>(null);
  const [repair, setRepair] = useState<ToolchainRepairSuggestionsResponse | null>(null);
  const [diagnostic, setDiagnostic] = useState<PostInstallCheckResponse | null>(null);
  const [diagnosticReport, setDiagnosticReport] = useState<DiagnosticReportResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isDiagnosticLoading, setIsDiagnosticLoading] = useState(false);
  const [copyMessage, setCopyMessage] = useState("");

  const loadStatus = useCallback(async (force = false) => {
    setIsLoading(true);
    try {
      if (force) await invoke<string>("refresh_runtime_cache");
      const rawPayload = await invoke<string>("get_toolchain_status");
      const nextStatus = normalizeResponse(rawPayload);
      setStatus(nextStatus);
      setRepair(buildRepairFromStatus(nextStatus));
    } catch (error) {
      setStatus(buildFrontendError(error));
      setRepair(buildFrontendRepairError(error));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus(false);
  }, [loadStatus]);

  const copyPythonPath = async () => {
    const pythonPath = status?.resolved_python?.path ?? "";
    if (!pythonPath) {
      setCopyMessage("当前没有可复制的 Python 路径。");
      return;
    }
    try {
      await navigator.clipboard.writeText(pythonPath);
      setCopyMessage("已复制当前 Python 路径。");
    } catch (error) {
      setCopyMessage(`复制失败，请手动选择路径：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const copyCommand = async (command: string) => {
    try {
      await navigator.clipboard.writeText(command);
      setCopyMessage("已复制命令。请在你信任的终端中手动执行。");
    } catch (error) {
      setCopyMessage(`复制失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const runDiagnostic = async () => {
    setIsDiagnosticLoading(true);
    try {
      const rawPayload = await invoke<string>("run_post_install_check");
      setDiagnostic(normalizePostInstallCheck(rawPayload));
    } catch (error) {
      setDiagnostic({
        ok: false,
        generated_at: "",
        app_version: "",
        os: { system: "", release: "", version: "", machine: "" },
        runtime_mode: "unknown",
        release_build_mode: "unknown",
        paths: { settings_path: "", resource_dir: "", toolchain_root: "" },
        tools: {},
        demo_projects: { ok: false, available: false, count: 0, demos: [] },
        modes: {
          basic_mode_available: false,
          assisted_mode_available: false,
          demo_mode_available: false,
          recommended_mode: "setup",
          next_action: "安装后自检失败，请重新打开应用后再试。",
        },
        issues: ["安装后自检失败。"],
        privacy_note: "",
        message: "安装后自检失败。",
        error: {
          code: "FRONTEND_DIAGNOSTIC_ERROR",
          message: "无法运行安装后自检。",
          raw_error: error instanceof Error ? error.message : String(error),
          suggestion: "请重新打开应用后重试；若仍失败，请查看原始诊断。",
        },
      });
    } finally {
      setIsDiagnosticLoading(false);
    }
  };

  const exportDiagnostic = async () => {
    setIsDiagnosticLoading(true);
    try {
      const rawPayload = await invoke<string>("export_diagnostic_report", { outputDir: "" });
      setDiagnosticReport(normalizeDiagnosticReport(rawPayload));
    } catch (error) {
      setCopyMessage(`诊断报告导出失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsDiagnosticLoading(false);
    }
  };

  return (
    <PageShell labelledBy="toolchain-status-title" className="toolchain-status-page">
      <OperationLoadingDialog
        open={isLoading || isDiagnosticLoading}
        title={translate(isDiagnosticLoading ? "正在运行本机自检" : "正在检测工具链")}
        message={translate(isDiagnosticLoading
          ? "正在核对随附资源、运行环境与示例状态。"
          : "正在加载 Vina、AutoGrid4、Python、RDKit 与 Meeko。")}
        detail={translate("首次加载科学工具时可能需要更长时间。")}
      />
      <PageHero
        eyebrow={translate("支持")}
        title={translate("配置工具链")}
        titleId="toolchain-status-title"
        description={translate("确认 Vina、AutoGrid4 和 Python 工具链是否可用。")}
        actions={
          <>
          <ActionButton variant="text" type="button" onClick={onBack}>{translate("返回")}</ActionButton>
          {onOpenSettings ? (
            <ActionButton variant="secondary" type="button" onClick={onOpenSettings}>{translate("配置路径")}</ActionButton>
          ) : null}
          <ActionButton variant="primary" type="button" onClick={() => void loadStatus(true)} disabled={isLoading}>
            {isLoading ? translate("检测中...") : translate("重新检测")}
          </ActionButton>
          </>
        }
      />

      <BodyGrid className="toolchain-status-body">
        <MainPanel>
          <div className="main-panel-content">
            {status ? (
              <>
          <div className="toolchain-wizard-grid">
            <article className="tool-card toolchain-wizard-card">
              <div className="tool-card-header">
                <div>
                  <h2>AutoDock Vina</h2>
                  <p>{translate("执行对接所需的外部命令行工具。")}</p>
                </div>
                <StatusBadge tone={statusTone(status.active_vina?.status)}>
                  {translate(statusText[status.active_vina?.status ?? "unknown"])}
                </StatusBadge>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>{translate("来源")}</dt>
                  <dd>{translate(sourceText[status.active_source] ?? sourceText.unknown)}</dd>
                </div>
                <div>
                  <dt>{translate("版本")}</dt>
                  <dd>{translate(status.active_vina?.version || status.bundled_vina.version || "未获取")}</dd>
                </div>
                <div>
                  <dt>{translate("路径")}</dt>
                  <dd>{pathOrEmpty(status.active_vina?.path || status.bundled_vina.path)}</dd>
                </div>
                <div>
                  <dt>{translate("建议")}</dt>
                  <dd>{translate(status.active_vina?.message || status.first_run_guidance?.recommended_action || "状态正常时即可继续创建项目。")}</dd>
                </div>
              </dl>
              <div className="toolbar">
                {onOpenSettings ? (
                  <ActionButton variant="secondary" type="button" onClick={onOpenSettings}>{translate("配置 Vina 路径")}</ActionButton>
                ) : null}
                {onOpenHelp ? (
                  <ActionButton variant="text" type="button" onClick={onOpenHelp}>{translate("查看工具链说明")}</ActionButton>
                ) : null}
              </div>
            </article>

            <article className="tool-card toolchain-wizard-card">
              <div className="tool-card-header">
                <div>
                  <h2>Python + RDKit + Meeko</h2>
                  <p>{translate("从原始结构生成 Vina 输入文件时需要这些 Python 工具。")}</p>
                </div>
                <StatusBadge tone={statusTone(status.resolved_python?.status)}>
                  {translate(statusText[status.resolved_python?.status ?? "unknown"])}
                </StatusBadge>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>{translate("Python 来源")}</dt>
                  <dd>{translate(sourceText[status.python_source] ?? sourceText.unknown)}</dd>
                </div>
                <div>
                  <dt>{translate("Python 路径")}</dt>
                  <dd>{pathOrEmpty(status.resolved_python?.path)}</dd>
                </div>
                <div>
                  <dt>RDKit</dt>
                  <dd>
                    <StatusBadge tone={statusTone(status.rdkit_for_python?.status)}>
                      {translate(statusText[status.rdkit_for_python?.status ?? "unknown"])}
                    </StatusBadge>
                    <span className="inline-meta">{translate(status.rdkit_for_python?.version || "未获取版本")}</span>
                  </dd>
                </div>
                <div>
                  <dt>Meeko</dt>
                  <dd>
                    <StatusBadge tone={statusTone(status.meeko_for_python?.status)}>
                      {translate(statusText[status.meeko_for_python?.status ?? "unknown"])}
                    </StatusBadge>
                    <span className="inline-meta">{translate(status.meeko_for_python?.version || "未获取版本")}</span>
                  </dd>
                </div>
              </dl>
              <div className="toolbar">
                {onOpenSettings ? (
                  <ActionButton variant="secondary" type="button" onClick={onOpenSettings}>{translate("配置 Python")}</ActionButton>
                ) : null}
                <ActionButton variant="text" type="button" onClick={copyPythonPath}>{translate("复制 Python 路径")}</ActionButton>
              </div>
              {copyMessage ? <p className="placeholder-note">{translate(copyMessage)}</p> : null}
              <p className="placeholder-note">
                {translate(status.manifest.includes_bundled_meeko === true && status.manifest.includes_bundled_rdkit === true
                  ? "Assisted 本地候选已随附固定 RDKit/Meeko；兼容的用户配置 Python 仍优先。运行时不会联网改环境。"
                  : "当前安装包不含 RDKit/Meeko；可配置独立 Python。运行时不会联网安装或修改系统环境。")}
              </p>
            </article>

            <article className="tool-card toolchain-wizard-card toolchain-optional-card">
              <div className="tool-card-header">
                <div>
                  <h2>AutoGrid4</h2>
                  <p>{translate("为 AutoDock4 maps、AD4Zn 与水合 AD4 生成网格。")}</p>
                </div>
                <StatusBadge tone={statusTone(status.autogrid4?.status)}>
                  {translate(statusText[status.autogrid4?.status ?? "unknown"])}
                </StatusBadge>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>{translate("来源")}</dt>
                  <dd>{translate(sourceText[status.autogrid4_source] ?? sourceText.unknown)}</dd>
                </div>
                <div>
                  <dt>{translate("版本")}</dt>
                  <dd>{translate(status.autogrid4?.version || "未获取")}</dd>
                </div>
                <div>
                  <dt>{translate("路径")}</dt>
                  <dd>{pathOrEmpty(status.autogrid4?.path)}</dd>
                </div>
                <div>
                  <dt>{translate("影响范围")}</dt>
                  <dd>{translate(status.autogrid4?.message || "缺失时仅 AutoDock4 maps 相关协议不可用。")}</dd>
                </div>
              </dl>
              <div className="toolbar">
                {onOpenSettings ? (
                  <ActionButton variant="secondary" type="button" onClick={onOpenSettings}>{translate("配置 AutoGrid4 路径")}</ActionButton>
                ) : null}
              </div>
              <p className="placeholder-note">{translate("外部 GPL 工具，不随 DockStart 安装包分发；缺失不影响 Vina / Vinardo 对接。")}</p>
            </article>

            <article className="tool-card toolchain-wizard-card">
              <div className="tool-card-header">
                <div>
                  <h2>{translate("随附资源")}</h2>
                  <p>
                    {translate(status.manifest.includes_bundled_meeko === true && status.manifest.includes_bundled_rdkit === true
                      ? "Assisted 包随附 Vina，以及独立、可替换的 RDKit/Meeko Python 工具环境。"
                      : "Basic 包随附 Vina 和 DockStart 运行所需的 Python，但不含 RDKit/Meeko。")}
                  </p>
                </div>
                <StatusBadge tone={packageStatusTone(status.bundled_vina.package_status)}>
                  {translate(packageStatusText[status.bundled_vina.package_status])}
                </StatusBadge>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>{translate("随附 Vina")}</dt>
                  <dd>{translate(booleanText(status.bundled_vina.exists))}，{translate(status.bundled_vina.version || "未获取版本")}</dd>
                </div>
                <div>
                  <dt>{translate("随附 Python（应用运行）")}</dt>
                  <dd>{translate(booleanText(status.bundled_python.exists))}，{translate(status.bundled_python.version || "未获取版本")}</dd>
                </div>
                <div>
                  <dt>{translate("许可证记录")}</dt>
                  <dd>{translate(booleanText(status.licenses.third_party_notices_exists))}</dd>
                </div>
                <div>
                  <dt>{translate("资源完整度")}</dt>
                  <dd>{translate(status.message || "暂无说明。")}</dd>
                </div>
              </dl>
            </article>
          </div>

          <details className="technical-details toolchain-resource-details">
            <summary>{translate("随附资源技术详情")}</summary>
            <dl className="tool-meta">
              <div>
                <dt>runtime_mode</dt>
                <dd>{translate(status.runtime_mode)}</dd>
              </div>
              <div>
                <dt>resource_dir</dt>
                <dd>{translate(pathOrEmpty(status.resource_dir))}</dd>
              </div>
              <div>
                <dt>toolchain_root</dt>
                <dd>{translate(pathOrEmpty(status.toolchain_root))}</dd>
              </div>
              <div>
                <dt>manifest</dt>
                <dd>{translate(pathOrEmpty(status.manifest_file))}（{translate(booleanText(status.manifest_exists))}）</dd>
              </div>
              <div>
                <dt>Vina sha256</dt>
                <dd aria-label={translate(status.bundled_vina.sha256 || "未记录")}>{translate(shortHash(status.bundled_vina.sha256))}</dd>
              </div>
              <div>
                <dt>Python sha256</dt>
                <dd aria-label={translate(status.bundled_python.sha256 || "未记录")}>{translate(shortHash(status.bundled_python.sha256))}</dd>
              </div>
              <div>
                <dt>manifest sha256</dt>
                <dd aria-label={translate(status.bundled_python_integrity?.manifest_sha256 || "未记录")}>
                  {translate(shortHash(status.bundled_python_integrity?.manifest_sha256 ?? ""))}
                </dd>
              </div>
              <div>
                <dt>Vina LICENSE</dt>
                <dd>{translate(pathOrEmpty(status.bundled_vina_integrity?.license_path))}</dd>
              </div>
            </dl>
            {status.manifest_error ? <pre>{status.manifest_error}</pre> : null}
            {status.bundled_vina.raw_error ? <pre>{status.bundled_vina.raw_error}</pre> : null}
            {status.bundled_python.raw_error ? <pre>{status.bundled_python.raw_error}</pre> : null}
          </details>

          <section className="mode-panel" aria-label={translate("工具链对使用模式的影响")}>
            <div className="mode-panel-header">
              <div>
                <span className="eyebrow">{translate("模式影响")}</span>
                <strong>{translate("缺什么，只影响对应路径")}</strong>
              </div>
              <StatusBadge tone={statusTone(status.active_vina?.status)}>
                {translate(status.active_vina?.status === "ok" ? "Basic Mode 可用" : "Basic Mode 需 Vina")}
              </StatusBadge>
            </div>
            <div className="compact-grid">
              <article className="metric-card">
                <span>Basic Mode</span>
                <strong>{translate(status.active_vina?.status === "ok" ? "可继续已有 PDBQT docking" : "需要先配置 Vina")}</strong>
                <p>{translate("只依赖 AutoDock Vina 和用户已有 receptor/ligand PDBQT。")}</p>
              </article>
              <article className="metric-card">
                <span>Assisted Mode</span>
                <strong>
                  {translate(status.rdkit_for_python?.status === "ok" && status.meeko_for_python?.status === "ok"
                    ? "可尝试 raw → PDBQT"
                    : "需要补齐 RDKit / Meeko")}
                </strong>
                <p>{translate("用于自动准备 PDBQT；缺失时不影响 Basic Mode。")}</p>
              </article>
            </div>
          </section>

          <section className="mode-panel" aria-label={translate("工具链修复建议")}>
            <div className="mode-panel-header">
              <div>
                <span className="eyebrow">{translate("修复建议")}</span>
                <strong>{translate("缺什么，就先修对应路径")}</strong>
              </div>
              {onOpenHelp ? (
                <ActionButton variant="text" type="button" onClick={onOpenHelp}>{translate("查看详细教程")}</ActionButton>
              ) : null}
            </div>
            {repair?.suggestions.length ? (
              <div className="compact-grid">
                {repair.suggestions.map((suggestion: ToolchainRepairSuggestion) => (
                  <article className="metric-card" key={suggestion.issue}>
                    <span>{translate(suggestion.affected_mode)}</span>
                    <strong>{translate(suggestion.recommended_fix)}</strong>
                    <p>{translate(suggestion.explanation)}</p>
                    <StatusBadge tone={severityTone(suggestion.severity)}>
                      {translate(severityText[suggestion.severity] ?? "提示")}
                    </StatusBadge>
                    {suggestion.manual_steps.length ? (
                      <details className="technical-details">
                        <summary>{translate("手动步骤")}</summary>
                        <ol>
                          {suggestion.manual_steps.map((step) => (
                            <li key={step}>{translate(step)}</li>
                          ))}
                        </ol>
                      </details>
                    ) : null}
                    {suggestion.copyable_commands.length ? (
                      <details className="technical-details">
                        <summary>{translate("可复制命令")}</summary>
                        <div className="command-list">
                          {suggestion.copyable_commands.map((command) => (
                            <div className="command-row" key={command}>
                              <code>{command}</code>
                              <ActionButton variant="text" type="button" onClick={() => void copyCommand(command)}>{translate("复制命令")}</ActionButton>
                            </div>
                          ))}
                        </div>
                      </details>
                    ) : null}
                  </article>
                ))}
              </div>
            ) : (
              <p className="placeholder-note">{translate(repair?.message || "暂无需要修复的关键工具链问题。")}</p>
            )}
            <p className="placeholder-note">{translate("这些建议不会自动安装工具，也不会修改系统 PATH。请确认命令含义后手动执行。")}</p>
          </section>

          <section className="mode-panel" aria-label={translate("安装后自检")}>
            <div className="mode-panel-header">
              <div>
                <span className="eyebrow">{translate("安装后自检")}</span>
                <strong>{translate("一键确认当前安装能完成哪条路径")}</strong>
              </div>
              <div className="toolbar">
                <ActionButton variant="secondary" type="button" onClick={() => void runDiagnostic()} disabled={isDiagnosticLoading}>
                  {translate(isDiagnosticLoading ? "检查中..." : "运行自检")}
                </ActionButton>
                <ActionButton variant="text" type="button" onClick={() => void exportDiagnostic()} disabled={isDiagnosticLoading}>{translate("导出诊断报告")}</ActionButton>
              </div>
            </div>
            {translate(diagnostic ? (
              <>
                <div className="compact-grid">
                  <article className="metric-card">
                    <span>Basic Mode</span>
                    <strong>{translate(modeAvailabilityText(diagnostic.modes.basic_mode_available))}</strong>
                    <p>{translate("已有 PDBQT 的最低依赖路径。")}</p>
                  </article>
                  <article className="metric-card">
                    <span>Assisted Mode</span>
                    <strong>{translate(modeAvailabilityText(diagnostic.modes.assisted_mode_available))}</strong>
                    <p>{translate("raw 文件自动准备 PDBQT。")}</p>
                  </article>
                  <article className="metric-card">
                    <span>Demo Mode</span>
                    <strong>{translate(modeAvailabilityText(diagnostic.modes.demo_mode_available))}</strong>
                    <p>{translate("示例项目数量：")}{diagnostic.demo_projects.count}</p>
                  </article>
                  <article className="metric-card">
                    <span>{translate("推荐下一步")}</span>
                    <strong>{translate(diagnostic.modes.recommended_mode)}</strong>
                    <p>{translate(diagnostic.modes.next_action)}</p>
                  </article>
                </div>
                {diagnostic.issues.length ? (
                  <details className="technical-details">
                    <summary>{translate("需要关注的问题")}</summary>
                    <ul>
                      {diagnostic.issues.map((issue) => (
                        <li key={issue}>{translate(issue)}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                <details className="technical-details">
                  <summary>{translate("诊断技术详情")}</summary>
                  <dl className="tool-meta">
                    <div>
                      <dt>{translate("版本")}</dt>
                      <dd>{translate(diagnostic.app_version)}</dd>
                    </div>
                    <div>
                      <dt>{translate("系统")}</dt>
                      <dd>{translate(diagnostic.os.system)} {translate(diagnostic.os.release)} {translate(diagnostic.os.machine)}</dd>
                    </div>
                    <div>
                      <dt>settings</dt>
                      <dd>{diagnostic.paths.settings_path}</dd>
                    </div>
                    <div>
                      <dt>resource_dir</dt>
                      <dd>{diagnostic.paths.resource_dir || translate("未设置")}</dd>
                    </div>
                  </dl>
                  <p className="placeholder-note">{translate(diagnostic.privacy_note)}</p>
                </details>
              </>
            ) : (
              <p className="placeholder-note">{translate("点击“运行自检”后，会显示当前安装是否满足 Basic / Assisted / Demo Mode。")}</p>
            ))}
            {diagnosticReport?.report_file ? (
              <p className="message-line">{translate("诊断报告已导出：")}{translate(diagnosticReport.report_file)}</p>
            ) : null}
          </section>

          {status.error ? (
            <div className="warning-note">
              {translate(status.error.message)}
              {translate(status.error.suggestion ? ` ${status.error.suggestion}` : "")}
            </div>
          ) : null}

          {status.warnings.length ? (
            <details className="technical-details">
              <summary>{translate("检查提示")}</summary>
              <strong>{translate("工具链检查提示")}</strong>
              <ul>
                {status.warnings.map((warning) => (
                  <li key={warning}>{translate(warning)}</li>
                ))}
              </ul>
            </details>
          ) : null}
              </>
            ) : (
              <p className="placeholder-note">{translate("正在读取工具链状态...")}</p>
            )}
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title={translate("当前可用性")}>
            <dl className="mode-context-list">
              <div>
                <dt>Vina</dt>
                <dd>{translate(statusText[status?.active_vina?.status ?? "unknown"])}</dd>
              </div>
              <div>
                <dt>Python</dt>
                <dd>{translate(statusText[status?.resolved_python?.status ?? "unknown"])}</dd>
              </div>
              <div>
                <dt>RDKit</dt>
                <dd>{translate(statusText[status?.rdkit_for_python?.status ?? "unknown"])}</dd>
              </div>
              <div>
                <dt>Meeko</dt>
                <dd>{translate(statusText[status?.meeko_for_python?.status ?? "unknown"])}</dd>
              </div>
              <div>
                <dt>AutoGrid4</dt>
                <dd>{translate(statusText[status?.autogrid4?.status ?? "unknown"])}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title={translate("路径影响")}>
            <p>{translate("基础模式主要依赖 Vina；结构准备还需要 Python、RDKit 和 Meeko。AutoGrid4 只影响 AutoDock4 maps 相关协议。")}</p>
          </RightRailSection>

          <RightRailSection title={translate("操作")}>
            <div className="button-row">
              {onOpenSettings ? (
                <ActionButton variant="secondary" type="button" onClick={onOpenSettings}>{translate("配置路径")}</ActionButton>
              ) : null}
              {onOpenHelp ? (
                <ActionButton variant="text" type="button" onClick={onOpenHelp}>{translate("查看帮助")}</ActionButton>
              ) : null}
            </div>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
