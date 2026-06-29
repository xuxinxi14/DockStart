import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ToolCheckResult, ToolSource, ToolStatus, ToolchainStatusResponse } from "../types";

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
  bundled: "DockStart 内置资源",
  configured: "用户配置路径",
  auto: "系统自动检测",
  current_environment: "Python 运行环境",
  frontend_dependency: "前端依赖",
  missing: "尚未找到",
  unknown: "未知来源",
};

const packageStatusText: Record<NonNullable<ToolchainStatusResponse["bundled_python_integrity"]>["status"], string> = {
  ready: "可用",
  incomplete: "待补全",
  missing: "未发现",
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

function buildFrontendError(error: unknown): ToolchainStatusResponse {
  const rawError = error instanceof Error ? error.message : String(error);
  const frontendTool: ToolCheckResult = {
    key: "toolchain",
    name: "工具链状态",
    status: "error",
    version: "",
    path: "",
    message: "前端未能调用工具链状态命令。",
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
    resolved_python: null,
    python_source: "unknown",
    meeko_for_python: null,
    rdkit_for_python: null,
    meeko_python_source: "unknown",
    rdkit_python_source: "unknown",
    first_run_guidance: {
      status: "unknown",
      recommended_action: "请在 Tauri 桌面端打开工具链页后重新检测。",
      primary_page: "toolchain-status",
      message: "前端无法读取后端工具链状态。",
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
      suggestion: "请确认当前运行环境是 Tauri 桌面端，并检查 Python 后端入口。",
    },
  };
}

function statusClass(status: ToolStatus | undefined): string {
  if (status === "ok") {
    return "status-ok";
  }
  if (status === "missing" || status === "unknown") {
    return "status-missing";
  }
  return "status-error";
}

function packageStatusClass(status: NonNullable<ToolchainStatusResponse["bundled_python_integrity"]>["status"]): string {
  if (status === "ready") {
    return "status-ok";
  }
  if (status === "incomplete") {
    return "status-missing";
  }
  return "status-error";
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

export default function ToolchainStatusPage({ onBack, onOpenHelp, onOpenSettings }: ToolchainStatusPageProps) {
  const [status, setStatus] = useState<ToolchainStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [copyMessage, setCopyMessage] = useState("");

  const loadStatus = useCallback(async () => {
    setIsLoading(true);
    try {
      const rawPayload = await invoke<string>("get_toolchain_status");
      setStatus(normalizeResponse(rawPayload));
    } catch (error) {
      setStatus(buildFrontendError(error));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
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

  return (
    <section className="workbench-page" aria-labelledby="toolchain-status-title">
      <header className="page-hero">
        <div className="page-hero-main">
          <p className="eyebrow">支持</p>
          <h1 id="toolchain-status-title">配置工具链</h1>
          <p>确认 Vina 和 Python 工具链是否可用。</p>
        </div>
        <div className="page-hero-actions">
          <button className="text-button" type="button" onClick={onBack}>返回</button>
          {onOpenSettings ? (
            <button className="secondary-button" type="button" onClick={onOpenSettings}>配置路径</button>
          ) : null}
          <button className="primary-button" type="button" onClick={loadStatus} disabled={isLoading}>
            {isLoading ? "检测中..." : "重新检测"}
          </button>
        </div>
      </header>

      {status ? (
        <>
          <div className="toolchain-wizard-grid">
            <article className="tool-card toolchain-wizard-card">
              <div className="tool-card-header">
                <div>
                  <h2>AutoDock Vina</h2>
                  <p>执行对接所需的外部命令行工具。</p>
                </div>
                <span className={`status-badge ${statusClass(status.active_vina?.status)}`}>
                  {statusText[status.active_vina?.status ?? "unknown"]}
                </span>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>来源</dt>
                  <dd>{sourceText[status.active_source] ?? sourceText.unknown}</dd>
                </div>
                <div>
                  <dt>版本</dt>
                  <dd>{status.active_vina?.version || status.bundled_vina.version || "未获取"}</dd>
                </div>
                <div>
                  <dt>路径</dt>
                  <dd>{pathOrEmpty(status.active_vina?.path || status.bundled_vina.path)}</dd>
                </div>
                <div>
                  <dt>建议</dt>
                  <dd>{status.active_vina?.message || status.first_run_guidance?.recommended_action || "状态正常时即可继续创建项目。"}</dd>
                </div>
              </dl>
              <div className="toolbar">
                {onOpenSettings ? (
                  <button className="secondary-button" type="button" onClick={onOpenSettings}>
                    配置 Vina 路径
                  </button>
                ) : null}
                {onOpenHelp ? (
                  <button className="text-button inline" type="button" onClick={onOpenHelp}>
                    查看工具链说明
                  </button>
                ) : null}
              </div>
            </article>

            <article className="tool-card toolchain-wizard-card">
              <div className="tool-card-header">
                <div>
                  <h2>Python + RDKit + Meeko</h2>
                  <p>从原始结构生成 Vina 输入文件时需要这些 Python 工具。</p>
                </div>
                <span className={`status-badge ${statusClass(status.resolved_python?.status)}`}>
                  {statusText[status.resolved_python?.status ?? "unknown"]}
                </span>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>Python 来源</dt>
                  <dd>{sourceText[status.python_source] ?? sourceText.unknown}</dd>
                </div>
                <div>
                  <dt>Python 路径</dt>
                  <dd>{pathOrEmpty(status.resolved_python?.path)}</dd>
                </div>
                <div>
                  <dt>RDKit</dt>
                  <dd>
                    <span className={`status-badge ${statusClass(status.rdkit_for_python?.status)}`}>
                      {statusText[status.rdkit_for_python?.status ?? "unknown"]}
                    </span>
                    <span className="inline-meta">{status.rdkit_for_python?.version || "未获取版本"}</span>
                  </dd>
                </div>
                <div>
                  <dt>Meeko</dt>
                  <dd>
                    <span className={`status-badge ${statusClass(status.meeko_for_python?.status)}`}>
                      {statusText[status.meeko_for_python?.status ?? "unknown"]}
                    </span>
                    <span className="inline-meta">{status.meeko_for_python?.version || "未获取版本"}</span>
                  </dd>
                </div>
              </dl>
              <div className="toolbar">
                {onOpenSettings ? (
                  <button className="secondary-button" type="button" onClick={onOpenSettings}>
                    配置 Python
                  </button>
                ) : null}
                <button className="text-button inline" type="button" onClick={copyPythonPath}>
                  复制 Python 路径
                </button>
              </div>
              {copyMessage ? <p className="placeholder-note">{copyMessage}</p> : null}
              <p className="placeholder-note">DockStart 只检测和调用已有环境，不自动安装 RDKit 或 Meeko。</p>
            </article>

            <article className="tool-card toolchain-wizard-card">
              <div className="tool-card-header">
                <div>
                  <h2>内置资源</h2>
                  <p>用于 Windows 打包版本的随附 Vina、Python 和许可证资源。</p>
                </div>
                <span className={`status-badge ${packageStatusClass(status.bundled_vina.package_status)}`}>
                  {packageStatusText[status.bundled_vina.package_status]}
                </span>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>随附 Vina</dt>
                  <dd>{booleanText(status.bundled_vina.exists)}，{status.bundled_vina.version || "未获取版本"}</dd>
                </div>
                <div>
                  <dt>随附 Python</dt>
                  <dd>{booleanText(status.bundled_python.exists)}，{status.bundled_python.version || "未获取版本"}</dd>
                </div>
                <div>
                  <dt>许可证记录</dt>
                  <dd>{booleanText(status.licenses.third_party_notices_exists)}</dd>
                </div>
                <div>
                  <dt>资源完整度</dt>
                  <dd>{status.message || "暂无说明。"}</dd>
                </div>
              </dl>
              <details className="technical-details">
                <summary>技术详情</summary>
                <dl className="tool-meta">
                  <div>
                    <dt>runtime_mode</dt>
                    <dd>{status.runtime_mode}</dd>
                  </div>
                  <div>
                    <dt>resource_dir</dt>
                    <dd>{pathOrEmpty(status.resource_dir)}</dd>
                  </div>
                  <div>
                    <dt>toolchain_root</dt>
                    <dd>{pathOrEmpty(status.toolchain_root)}</dd>
                  </div>
                  <div>
                    <dt>manifest</dt>
                    <dd>{pathOrEmpty(status.manifest_file)}（{booleanText(status.manifest_exists)}）</dd>
                  </div>
                  <div>
                    <dt>Vina sha256</dt>
                    <dd title={status.bundled_vina.sha256}>{shortHash(status.bundled_vina.sha256)}</dd>
                  </div>
                  <div>
                    <dt>Python sha256</dt>
                    <dd title={status.bundled_python.sha256}>{shortHash(status.bundled_python.sha256)}</dd>
                  </div>
                  <div>
                    <dt>manifest sha256</dt>
                    <dd title={status.bundled_python_integrity?.manifest_sha256 ?? ""}>
                      {shortHash(status.bundled_python_integrity?.manifest_sha256 ?? "")}
                    </dd>
                  </div>
                  <div>
                    <dt>Vina LICENSE</dt>
                    <dd>{pathOrEmpty(status.bundled_vina_integrity?.license_path)}</dd>
                  </div>
                </dl>
                {status.manifest_error ? <pre>{status.manifest_error}</pre> : null}
                {status.bundled_vina.raw_error ? <pre>{status.bundled_vina.raw_error}</pre> : null}
                {status.bundled_python.raw_error ? <pre>{status.bundled_python.raw_error}</pre> : null}
              </details>
            </article>
          </div>

          <section className="mode-panel" aria-label="工具链对使用模式的影响">
            <div className="mode-panel-header">
              <div>
                <span className="eyebrow">模式影响</span>
                <strong>缺什么，只影响对应路径</strong>
              </div>
              <span className={`status-badge ${statusClass(status.active_vina?.status)}`}>
                {status.active_vina?.status === "ok" ? "Basic Mode 可用" : "Basic Mode 需 Vina"}
              </span>
            </div>
            <div className="compact-grid">
              <article className="metric-card">
                <span>Basic Mode</span>
                <strong>{status.active_vina?.status === "ok" ? "可继续已有 PDBQT docking" : "需要先配置 Vina"}</strong>
                <p>只依赖 AutoDock Vina 和用户已有 receptor/ligand PDBQT。</p>
              </article>
              <article className="metric-card">
                <span>Assisted Mode</span>
                <strong>
                  {status.rdkit_for_python?.status === "ok" && status.meeko_for_python?.status === "ok"
                    ? "可尝试 raw → PDBQT"
                    : "需要补齐 RDKit / Meeko"}
                </strong>
                <p>用于自动准备 PDBQT；缺失时不影响 Basic Mode。</p>
              </article>
            </div>
          </section>

          {status.error ? (
            <div className="warning-note">
              {status.error.message}
              {status.error.suggestion ? ` ${status.error.suggestion}` : ""}
            </div>
          ) : null}

          {status.warnings.length ? (
            <details className="technical-details">
              <summary>检查提示</summary>
              <strong>工具链检查提示</strong>
              <ul>
                {status.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : (
        <p className="placeholder-note">正在读取工具链状态...</p>
      )}
    </section>
  );
}
