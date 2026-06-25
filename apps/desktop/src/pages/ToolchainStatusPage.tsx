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
  ready: "ready：内置工具链可用",
  partial: "partial：目录已建立，但工具链尚未完整",
  missing: "missing：工具链目录缺失",
};

function normalizeTool(item: Partial<ToolCheckResult> | null | undefined): ToolCheckResult | null {
  if (!item) {
    return null;
  }
  return {
    key: item.key ?? "vina",
    name: item.name ?? "AutoDock Vina",
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

function normalizeResponse(rawPayload: string): ToolchainStatusResponse {
  const parsed = JSON.parse(rawPayload) as Partial<ToolchainStatusResponse>;
  return {
    ok: Boolean(parsed.ok),
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
    },
    active_vina: normalizeTool(parsed.active_vina),
    active_source: parsed.active_source ?? "unknown",
    licenses: {
      exists: Boolean(parsed.licenses?.exists),
      third_party_notices: parsed.licenses?.third_party_notices ?? "",
      third_party_notices_exists: Boolean(parsed.licenses?.third_party_notices_exists),
    },
    resources: {
      exists: Boolean(parsed.resources?.exists),
      tools_dir_exists: Boolean(parsed.resources?.tools_dir_exists),
      vina_dir_exists: Boolean(parsed.resources?.vina_dir_exists),
    },
    full_status: parsed.full_status ?? "missing",
    message: parsed.message ?? "",
    error: parsed.error,
  };
}

function buildFrontendError(error: unknown): ToolchainStatusResponse {
  const rawError = error instanceof Error ? error.message : String(error);
  return {
    ok: false,
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
      message: "前端未能调用内置工具链状态命令。",
      raw_error: rawError,
    },
    active_vina: null,
    active_source: "unknown",
    licenses: {
      exists: false,
      third_party_notices: "",
      third_party_notices_exists: false,
    },
    resources: {
      exists: false,
      tools_dir_exists: false,
      vina_dir_exists: false,
    },
    full_status: "missing",
    message: "读取内置工具链状态失败。",
    error: {
      code: "FRONTEND_TOOLCHAIN_STATUS_ERROR",
      message: "前端未能调用内置工具链状态命令。",
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
          这里只检查 DockStart 的内置工具链目录和 AutoDock Vina 解析来源。
          当前版本不会下载工具，不会生成 PDBQT，也不会改变 docking 主流程。
        </p>
      </div>

      <div className="toolbar">
        <button className="primary-button" type="button" onClick={loadStatus} disabled={isLoading}>
          {isLoading ? "读取中..." : "重新读取状态"}
        </button>
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
                  <dt>tools 目录</dt>
                  <dd>{status.tools_dir || "未获取"}（{booleanText(status.resources.tools_dir_exists)}）</dd>
                </div>
                <div>
                  <dt>manifest</dt>
                  <dd>{status.manifest_file || "未获取"}（{booleanText(status.manifest_exists)}）</dd>
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
                  <dt>路径</dt>
                  <dd>{status.bundled_vina.path || "未获取"}</dd>
                </div>
                <div>
                  <dt>版本</dt>
                  <dd>{status.bundled_vina.version || "未获取"}</dd>
                </div>
                <div>
                  <dt>说明</dt>
                  <dd>{status.bundled_vina.message}</dd>
                </div>
              </dl>
              {status.bundled_vina.raw_error ? (
                <details className="raw-error">
                  <summary>查看 bundled Vina raw_error</summary>
                  <pre>{status.bundled_vina.raw_error}</pre>
                </details>
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
              {status.active_vina?.raw_error ? (
                <details className="raw-error">
                  <summary>查看 active Vina raw_error</summary>
                  <pre>{status.active_vina.raw_error}</pre>
                </details>
              ) : null}
            </article>

            <article className="tool-card">
              <div className="tool-card-header">
                <h2>许可证目录</h2>
              </div>
              <dl className="tool-meta">
                <div>
                  <dt>resources/licenses</dt>
                  <dd>{status.licenses_dir || "未获取"}（{booleanText(status.licenses.exists)}）</dd>
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
        </>
      ) : (
        <p className="placeholder-note">正在读取内置工具链状态...</p>
      )}
    </section>
  );
}
