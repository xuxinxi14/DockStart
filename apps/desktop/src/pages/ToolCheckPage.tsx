import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ToolCheckResult, ToolSource, ToolStatus } from "../types";

type ToolCheckPageProps = {
  onOpenSettings: () => void;
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

function normalizeResult(item: Partial<ToolCheckResult>): ToolCheckResult {
  return {
    key: item.key ?? "unknown",
    name: item.name ?? "未知工具",
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

function buildFrontendError(error: unknown): ToolCheckResult[] {
  return [
    {
      key: "tool_check_bridge",
      name: "工具检测入口",
      status: "error",
      version: "",
      path: "",
      message:
        "前端未能调用 Tauri 工具检测命令。请确认当前运行环境是 Tauri 桌面端，并检查 Python 后端入口。",
      raw_error: error instanceof Error ? error.message : String(error),
      source: "unknown",
      bundled_path: "",
      is_bundled: false,
    },
  ];
}

export default function ToolCheckPage({ onOpenSettings }: ToolCheckPageProps) {
  const [results, setResults] = useState<ToolCheckResult[]>([]);
  const [isChecking, setIsChecking] = useState(false);

  const runCheck = useCallback(async () => {
    setIsChecking(true);
    try {
      const rawPayload = await invoke<string>("check_tools");
      const parsed = JSON.parse(rawPayload);
      if (!Array.isArray(parsed)) {
        throw new Error("工具检测返回值不是 JSON 数组。");
      }
      setResults(parsed.map(normalizeResult));
    } catch (error) {
      setResults(buildFrontendError(error));
    } finally {
      setIsChecking(false);
    }
  }, []);

  useEffect(() => {
    void runCheck();
  }, [runCheck]);

  return (
    <section className="tool-check" aria-labelledby="tool-check-title">
      <div className="page-heading">
        <p className="eyebrow">ToolCheckPage</p>
        <h1 id="tool-check-title">工具检测</h1>
        <p>
          这一步只确认本机是否具备 DockStart MVP 需要的运行环境。检测不会下载数据库，
          不会准备分子文件，也不会运行 docking。
        </p>
      </div>

      <div className="toolbar">
        <button className="secondary-button" type="button" onClick={onOpenSettings}>
          配置工具路径
        </button>
        <button className="primary-button" type="button" onClick={runCheck} disabled={isChecking}>
          {isChecking ? "检测中..." : "重新检测"}
        </button>
      </div>

      <div className="tool-grid">
        {results.map((tool) => (
          <article className="tool-card" key={tool.key}>
            <div className="tool-card-header">
              <h2>{tool.name}</h2>
              <span className={`status-badge status-${tool.status}`}>
                {statusText[tool.status] ?? statusText.unknown}
              </span>
            </div>

            <dl className="tool-meta">
              <div>
                <dt>版本</dt>
                <dd>{tool.version || "未获取"}</dd>
              </div>
              <div>
                <dt>路径</dt>
                <dd>{tool.path || "未检测到路径"}</dd>
              </div>
              <div>
                <dt>来源</dt>
                <dd>{sourceText[tool.source] ?? sourceText.unknown}</dd>
              </div>
              {tool.bundled_path ? (
                <div>
                  <dt>内置 Vina 路径</dt>
                  <dd>{tool.bundled_path}</dd>
                </div>
              ) : null}
              <div>
                <dt>说明</dt>
                <dd>{tool.message}</dd>
              </div>
            </dl>

            {tool.raw_error ? (
              <details className="raw-error">
                <summary>查看 raw_error</summary>
                <pre>{tool.raw_error}</pre>
              </details>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}
