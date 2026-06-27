import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ToolCheckResult, ToolSource, ToolStatus, ToolchainStatusResponse } from "../types";

type ToolchainStatusPageProps = {
  onBack: () => void;
};

const statusText: Record<ToolStatus, string> = {
  ok: "已检测",
  missing: "未检测",
  error: "检测错误",
  unknown: "状态未知",
};

const sourceText: Record<ToolSource, string> = {
  bundled: "内置工具链",
  configured: "用户配置",
  auto: "自动检测",
  current_environment: "当前环境",
  frontend_dependency: "前端依赖",
  missing: "未找到来源",
  unknown: "未知来源",
};

const fullStatusText: Record<ToolchainStatusResponse["full_status"], string> = {
  ready: "ready：内置 Vina 打包条件已满足",
  partial: "partial：工具链目录已建立，但 Full 工具链尚未完整",
  missing: "missing：工具链资源目录缺失",
};

const runtimeModeText: Record<ToolchainStatusResponse["runtime_mode"], string> = {
  dev: "dev：开发环境，使用项目根目录下的 resources/",
  packaged: "packaged：打包环境，使用 Tauri resource_dir 下的 resources/",
  unknown: "unknown：无法判断运行模式",
};

const packageStatusText: Record<NonNullable<ToolchainStatusResponse["bundled_python_integrity"]>["status"], string> = {
  ready: "ready：可用于 Full 打包",
  incomplete: "incomplete：文件存在，但 manifest 或完整性信息尚未完整",
  missing: "missing：未发现内置二进制",
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
    message: "前端未能调用内置工具链状态命令。",
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
    message: "读取内置工具链状态失败。",
    error: {
      code: "FRONTEND_TOOLCHAIN_STATUS_ERROR",
      message: frontendTool.message,
      raw_error: rawError,
      suggestion: "请确认当前运行环境是 Tauri 桌面端，并检查 Python 后端入口。",
    },
  };
}

function booleanText(value: boolean): string {
  return value ? "存在" : "不存在";
}

function fullStatusClass(status: ToolchainStatusResponse["full_status"]): string {
  if (status === "ready") {
    return "status-ok";
  }
  if (status === "partial") {
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

function shortHash(value: string): string {
  return value ? `${value.slice(0, 16)}...` : "未计算";
}

export default function ToolchainStatusPage({ onBack }: ToolchainStatusPageProps) {
  const [status, setStatus] = useState<ToolchainStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);

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

  return (
    <section className="toolchain-status-page" aria-labelledby="toolchain-status-title">
      <button className="text-button" type="button" onClick={onBack}>
        返回
      </button>

      <div className="page-heading">
        <p className="eyebrow">ToolchainStatusPage</p>
        <h1 id="toolchain-status-title">内置工具链状态</h1>
        <p>
          这里只检查 DockStart 的内置工具链目录、AutoDock Vina 和 Python runtime 解析状态。
          当前版本不会下载工具、不会安装 Python 包，也不会改变 docking 主流程。
        </p>
      </div>

      <div className="toolbar">
        <button className="primary-button" type="button" onClick={loadStatus} disabled={isLoading}>
          {isLoading ? "读取中..." : "重新读取状态"}
        </button>
      </div>

      <div className="disclaimer-note">
        本页只负责检测 Meeko / RDKit / Python 工具链是否可用，不会在这里处理分子或生成 PDBQT。
        如果工具链满足条件，PDBQT 自动准备需要用户进入 PreparationPage 后手动点击准备按钮；生成结果仍需人工检查。
      </div>

      {status ? (
        <>
          <div className="summary-grid">
            <article className="tool-card">
              <div className="tool-card-header">
                <h2>Full 工具链状态</h2>
                <span className={`status-badge ${fullStatusClass(status.full_status)}`}>
                  {status.full_status}
                </span>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>当前运行模式</dt>
                  <dd>{runtimeModeText[status.runtime_mode]}</dd>
                </div>
                <div>
                  <dt>状态说明</dt>
                  <dd>{fullStatusText[status.full_status]}</dd>
                </div>
                <div>
                  <dt>后端说明</dt>
                  <dd>{status.message || "暂无说明。"}</dd>
                </div>
              </dl>
            </article>

            <article className="tool-card">
              <div className="tool-card-header">
                <h2>工具链目录</h2>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>resources</dt>
                  <dd>{status.toolchain_root || "未获取"}</dd>
                </div>
                <div>
                  <dt>Tauri resource_dir</dt>
                  <dd>{status.resource_dir || "开发环境未使用 DOCKSTART_RESOURCE_DIR"}</dd>
                </div>
                <div>
                  <dt>tools 目录</dt>
                  <dd>
                    {status.tools_dir || "未获取"}（{booleanText(status.resources.tools_dir_exists)}）
                  </dd>
                </div>
                <div>
                  <dt>Python 目录</dt>
                  <dd>
                    {status.bundled_python.path ? status.bundled_python.path.replace(/[/\\]python\.exe$/i, "") : "未获取"}（
                    {booleanText(status.resources.python_dir_exists)}）
                  </dd>
                </div>
                <div>
                  <dt>manifest</dt>
                  <dd>
                    {status.manifest_file || "未获取"}（{booleanText(status.manifest_exists)}）
                  </dd>
                </div>
              </dl>
              {status.manifest_error ? (
                <details className="raw-error">
                  <summary>查看 manifest_error</summary>
                  <pre>{status.manifest_error}</pre>
                </details>
              ) : null}
            </article>

            <article className="tool-card">
              <div className="tool-card-header">
                <h2>Bundled Vina</h2>
                <span className={`status-badge status-${status.bundled_vina.status}`}>
                  {statusText[status.bundled_vina.status] ?? statusText.unknown}
                </span>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>是否存在</dt>
                  <dd>{booleanText(status.bundled_vina.exists)}</dd>
                </div>
                <div>
                  <dt>Full 打包状态</dt>
                  <dd>{packageStatusText[status.bundled_vina.package_status]}</dd>
                </div>
                <div>
                  <dt>路径</dt>
                  <dd>{status.bundled_vina.path || "未获取"}</dd>
                </div>
                <div>
                  <dt>版本</dt>
                  <dd>{status.bundled_vina.version || "未获取"}</dd>
                </div>
                <div>
                  <dt>sha256</dt>
                  <dd title={status.bundled_vina.sha256}>{shortHash(status.bundled_vina.sha256)}</dd>
                </div>
                <div>
                  <dt>来源记录</dt>
                  <dd>{status.bundled_vina_integrity?.manifest_source || "manifest 尚未记录来源"}</dd>
                </div>
                <div>
                  <dt>LICENSE</dt>
                  <dd>
                    {status.bundled_vina_integrity?.license_path || "未获取"}（
                    {booleanText(Boolean(status.bundled_vina_integrity?.license_exists))}）
                  </dd>
                </div>
                <div>
                  <dt>THIRD_PARTY_NOTICES 记录</dt>
                  <dd>{booleanText(Boolean(status.bundled_vina_integrity?.third_party_notices_has_autodock_vina))}</dd>
                </div>
              </dl>
              {!status.bundled_vina.exists ? (
                <p className="placeholder-note">
                  未发现 bundled Vina。可以把本地 vina.exe 准备到 resources/vina/，或在设置页配置外部 AutoDock Vina。
                </p>
              ) : null}
            </article>

            <article className="tool-card">
              <div className="tool-card-header">
                <h2>当前实际使用的 Vina</h2>
                <span className={`status-badge status-${status.active_vina?.status ?? "unknown"}`}>
                  {statusText[status.active_vina?.status ?? "unknown"]}
                </span>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>来源</dt>
                  <dd>{sourceText[status.active_source] ?? sourceText.unknown}</dd>
                </div>
                <div>
                  <dt>路径</dt>
                  <dd>{status.active_vina?.path || "未检测到路径"}</dd>
                </div>
                <div>
                  <dt>版本</dt>
                  <dd>{status.active_vina?.version || "未获取"}</dd>
                </div>
                <div>
                  <dt>说明</dt>
                  <dd>{status.active_vina?.message || "暂无说明。"}</dd>
                </div>
              </dl>
            </article>

            <article className="tool-card">
              <div className="tool-card-header">
                <h2>Bundled Python</h2>
                <span className={`status-badge status-${status.bundled_python.status}`}>
                  {statusText[status.bundled_python.status] ?? statusText.unknown}
                </span>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>是否存在</dt>
                  <dd>{booleanText(status.bundled_python.exists)}</dd>
                </div>
                <div>
                  <dt>Full 打包状态</dt>
                  <dd>
                    <span className={`status-badge ${packageStatusClass(status.bundled_python.package_status)}`}>
                      {packageStatusText[status.bundled_python.package_status]}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>路径</dt>
                  <dd>{status.bundled_python.path || "未获取"}</dd>
                </div>
                <div>
                  <dt>版本</dt>
                  <dd>{status.bundled_python.version || status.bundled_python_integrity?.manifest_version || "未获取"}</dd>
                </div>
                <div>
                  <dt>sha256</dt>
                  <dd title={status.bundled_python.sha256}>{shortHash(status.bundled_python.sha256)}</dd>
                </div>
                <div>
                  <dt>manifest sha256</dt>
                  <dd title={status.bundled_python_integrity?.manifest_sha256 ?? ""}>
                    {shortHash(status.bundled_python_integrity?.manifest_sha256 ?? "")}
                  </dd>
                </div>
                <div>
                  <dt>说明</dt>
                  <dd>{status.bundled_python.message}</dd>
                </div>
              </dl>
              {status.bundled_python.raw_error ? (
                <details className="raw-error">
                  <summary>查看 bundled Python raw_error</summary>
                  <pre>{status.bundled_python.raw_error}</pre>
                </details>
              ) : null}
            </article>

            <article className="tool-card">
              <div className="tool-card-header">
                <h2>Python / Meeko / RDKit 检测来源</h2>
                <span className={`status-badge status-${status.resolved_python?.status ?? "unknown"}`}>
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
                  <dd>{status.resolved_python?.path || "未检测到路径"}</dd>
                </div>
                <div>
                  <dt>Python 版本</dt>
                  <dd>{status.resolved_python?.version || "未获取"}</dd>
                </div>
                <div>
                  <dt>Meeko 检测使用来源</dt>
                  <dd>
                    {sourceText[status.meeko_python_source] ?? sourceText.unknown}；
                    {statusText[status.meeko_for_python?.status ?? "unknown"]}
                  </dd>
                </div>
                <div>
                  <dt>RDKit 检测使用来源</dt>
                  <dd>
                    {sourceText[status.rdkit_python_source] ?? sourceText.unknown}；
                    {statusText[status.rdkit_for_python?.status ?? "unknown"]}
                  </dd>
                </div>
              </dl>
              <p className="placeholder-note">
                本页面只做 import 和能力检测，不安装 Python 包；真正的 Meeko/RDKit preparation 只在 PreparationPage 中由用户手动触发。
              </p>
            </article>

            <article className="tool-card">
              <div className="tool-card-header">
                <h2>许可证目录</h2>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>resources/licenses</dt>
                  <dd>
                    {status.licenses_dir || "未获取"}（{booleanText(status.licenses.exists)}）
                  </dd>
                </div>
                <div>
                  <dt>THIRD_PARTY_NOTICES.md</dt>
                  <dd>
                    {status.licenses.third_party_notices || "未获取"}（
                    {booleanText(status.licenses.third_party_notices_exists)}）
                  </dd>
                </div>
              </dl>
            </article>
          </div>

          {status.error ? (
            <div className="warning-note">
              {status.error.message}
              {status.error.suggestion ? ` ${status.error.suggestion}` : ""}
            </div>
          ) : null}

          {status.warnings.length ? (
            <div className="warning-note">
              <strong>内置工具链检查提示</strong>
              <ul>
                {status.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : (
        <p className="placeholder-note">正在读取内置工具链状态...</p>
      )}
    </section>
  );
}
