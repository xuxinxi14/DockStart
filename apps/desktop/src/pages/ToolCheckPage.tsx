import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ActionButton from "../components/ActionButton";
import AdvancedDetails from "../components/AdvancedDetails";
import { BodyGrid, MainPanel, PageHero, PageShell, RightRail, RightRailSection } from "../components/layout/PageLayout";
import SectionCard from "../components/SectionCard";
import StatusBadge from "../components/StatusBadge";
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
  bundled: "随应用提供",
  configured: "用户配置",
  auto: "自动检测",
  current_environment: "Python 运行环境",
  frontend_dependency: "前端依赖",
  missing: "未找到来源",
  unknown: "未知来源",
};

function statusTone(status: ToolStatus): "ok" | "warning" | "error" | "muted" {
  if (status === "ok") return "ok";
  if (status === "missing") return "warning";
  if (status === "error") return "error";
  return "muted";
}

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

  const runCheck = useCallback(async (force = false) => {
    setIsChecking(true);
    try {
      if (force) await invoke<string>("refresh_runtime_cache");
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
    void runCheck(false);
  }, [runCheck]);

  const detectedCount = results.filter((tool) => tool.status === "ok").length;
  const attentionCount = results.filter((tool) => tool.status !== "ok").length;
  const vinaResult = results.find((tool) => tool.key === "vina");
  const assistedKeys = ["vina", "python", "rdkit", "meeko"];
  const assistedResults = assistedKeys.map((key) => results.find((tool) => tool.key === key));
  const assistedReady = assistedResults.every((tool) => tool?.status === "ok");

  return (
    <PageShell labelledBy="tool-check-title">
      <PageHero
        eyebrow="运行环境"
        title="工具检测"
        titleId="tool-check-title"
        description="确认本机运行环境；检测不会下载数据库、准备分子文件或运行 docking。"
        actions={
          <>
            <ActionButton onClick={onOpenSettings}>配置工具路径</ActionButton>
            <ActionButton variant="primary" onClick={() => void runCheck(true)} disabled={isChecking}>
              {isChecking ? "检测中..." : "重新检测"}
            </ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <div className="status-strip">
              <article className="metric-card">
                <span>检测项目</span>
                <strong>{results.length || "等待结果"}</strong>
                <StatusBadge tone={isChecking ? "info" : "muted"}>{isChecking ? "检测中" : "本机环境"}</StatusBadge>
              </article>
              <article className="metric-card">
                <span>已检测</span>
                <strong>{detectedCount}</strong>
                <StatusBadge tone={detectedCount > 0 ? "ok" : "muted"}>{detectedCount > 0 ? "可用" : "尚无结果"}</StatusBadge>
              </article>
              <article className="metric-card">
                <span>需要处理</span>
                <strong>{attentionCount}</strong>
                <StatusBadge tone={results.length === 0 ? "muted" : attentionCount > 0 ? "warning" : "ok"}>
                  {results.length === 0 ? "等待结果" : attentionCount > 0 ? "请检查" : "无异常"}
                </StatusBadge>
              </article>
            </div>

            <SectionCard title="检测结果" description="路径、版本和来源会决定 DockStart 实际调用哪套工具链。">
              {results.length === 0 ? <p className="placeholder-note">正在读取本机工具状态。</p> : null}
              <div className="tool-grid">
                {results.map((tool) => (
                  <article className="tool-card" key={tool.key}>
                    <div className="tool-card-header">
                      <h2>{tool.name}</h2>
                      <StatusBadge tone={statusTone(tool.status)}>
                        {statusText[tool.status] ?? statusText.unknown}
                      </StatusBadge>
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
                          <dt>随附 Vina 路径</dt>
                          <dd>{tool.bundled_path}</dd>
                        </div>
                      ) : null}
                      <div>
                        <dt>说明</dt>
                        <dd>{tool.message}</dd>
                      </div>
                    </dl>

                    {tool.raw_error ? (
                      <AdvancedDetails>
                        <pre>{tool.raw_error}</pre>
                      </AdvancedDetails>
                    ) : null}
                  </article>
                ))}
              </div>
            </SectionCard>
          </div>
        </MainPanel>

        <RightRail>
          <RightRailSection title="本次检测">
            <dl className="mode-context-list">
              <div>
                <dt>状态</dt>
                <dd>{isChecking ? "正在检测" : results.length > 0 ? "检测已完成" : "等待检测"}</dd>
              </div>
              <div>
                <dt>已检测</dt>
                <dd>{detectedCount} 项</dd>
              </div>
              <div>
                <dt>需要处理</dt>
                <dd>{attentionCount} 项</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="模式影响">
            <dl className="mode-context-list">
              <div>
                <dt>Basic Mode</dt>
                <dd>
                  {isChecking || results.length === 0
                    ? "正在确认 Vina 状态"
                    : vinaResult?.status === "ok"
                      ? "Vina 已就绪"
                      : "需要可用的 AutoDock Vina"}
                </dd>
              </div>
              <div>
                <dt>Assisted Mode</dt>
                <dd>
                  {isChecking || results.length === 0
                    ? "正在确认准备工具链"
                    : assistedReady
                      ? "Vina、Python、RDKit、Meeko 已就绪"
                      : "需要检查 Vina、Python、RDKit、Meeko"}
                </dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title="修复顺序">
            <p>先重新检测随附 Vina 与 Assisted Python；仍不可用时再配置外部路径。RDKit / Meeko 缺失只影响自动准备。</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
