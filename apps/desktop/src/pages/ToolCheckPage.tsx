import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
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
  frontend_dependency: "应用内置资源",
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
      name: "工具状态",
      status: "error",
      version: "",
      path: "",
      message: "DockStart 无法读取工具状态。请重新打开应用后再试。",
      raw_error: error instanceof Error ? error.message : String(error),
      source: "unknown",
      bundled_path: "",
      is_bundled: false,
    },
  ];
}

export default function ToolCheckPage({ onOpenSettings }: ToolCheckPageProps) {
  useLanguage();
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
        eyebrow={translate("运行环境")}
        title={translate("工具检测")}
        titleId="tool-check-title"
        description={translate("确认本机运行环境；检测不会下载数据库、准备分子文件或运行 docking。")}
        actions={
          <>
            <ActionButton onClick={onOpenSettings}>{translate("配置工具路径")}</ActionButton>
            <ActionButton variant="primary" onClick={() => void runCheck(true)} disabled={isChecking}>
              {isChecking ? translate("检测中...") : translate("重新检测")}
            </ActionButton>
          </>
        }
      />

      <BodyGrid>
        <MainPanel>
          <div className="main-panel-content">
            <div className="status-strip">
              <article className="metric-card">
                <span>{translate("检测项目")}</span>
                <strong>{translate(results.length || "等待结果")}</strong>
                <StatusBadge tone={isChecking ? "info" : "muted"}>{translate(isChecking ? "检测中" : "本机环境")}</StatusBadge>
              </article>
              <article className="metric-card">
                <span>{translate("已检测")}</span>
                <strong>{detectedCount}</strong>
                <StatusBadge tone={detectedCount > 0 ? "ok" : "muted"}>{translate(detectedCount > 0 ? "可用" : "尚无结果")}</StatusBadge>
              </article>
              <article className="metric-card">
                <span>{translate("需要处理")}</span>
                <strong>{attentionCount}</strong>
                <StatusBadge tone={results.length === 0 ? "muted" : attentionCount > 0 ? "warning" : "ok"}>
                  {translate(results.length === 0 ? "等待结果" : attentionCount > 0 ? "请检查" : "无异常")}
                </StatusBadge>
              </article>
            </div>

            <SectionCard title={translate("检测结果")} description={translate("路径、版本和来源会决定 DockStart 实际调用哪套工具链。")}>
              {results.length === 0 ? <p className="placeholder-note">{translate("正在读取本机工具状态。")}</p> : null}
              <div className="tool-grid">
                {results.map((tool) => (
                  <article className="tool-card" key={tool.key}>
                    <div className="tool-card-header">
                      <h2>{tool.name}</h2>
                      <StatusBadge tone={statusTone(tool.status)}>
                        {translate(statusText[tool.status] ?? statusText.unknown)}
                      </StatusBadge>
                    </div>

                    <dl className="tool-meta">
                      <div>
                        <dt>{translate("版本")}</dt>
                        <dd>{translate(tool.version || "未获取")}</dd>
                      </div>
                      <div>
                        <dt>{translate("路径")}</dt>
                        <dd>{tool.path || translate("未检测到路径")}</dd>
                      </div>
                      <div>
                        <dt>{translate("来源")}</dt>
                        <dd>{translate(sourceText[tool.source] ?? sourceText.unknown)}</dd>
                      </div>
                      {tool.bundled_path ? (
                        <div>
                          <dt>{translate("随附 Vina 路径")}</dt>
                          <dd>{translate(tool.bundled_path)}</dd>
                        </div>
                      ) : null}
                      <div>
                        <dt>{translate("说明")}</dt>
                        <dd>{translate(tool.message)}</dd>
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
          <RightRailSection title={translate("本次检测")}>
            <dl className="mode-context-list">
              <div>
                <dt>{translate("状态")}</dt>
                <dd>{translate(isChecking ? "正在检测" : results.length > 0 ? "检测已完成" : "等待检测")}</dd>
              </div>
              <div>
                <dt>{translate("已检测")}</dt>
                <dd>{detectedCount}{translate(" 项")}</dd>
              </div>
              <div>
                <dt>{translate("需要处理")}</dt>
                <dd>{attentionCount}{translate(" 项")}</dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title={translate("模式影响")}>
            <dl className="mode-context-list">
              <div>
                <dt>Basic Mode</dt>
                <dd>
                  {translate(isChecking || results.length === 0
                    ? "正在确认 Vina 状态"
                    : vinaResult?.status === "ok"
                      ? "Vina 已就绪"
                      : "需要可用的 AutoDock Vina")}
                </dd>
              </div>
              <div>
                <dt>Assisted Mode</dt>
                <dd>
                  {translate(isChecking || results.length === 0
                    ? "正在确认准备工具链"
                    : assistedReady
                      ? "Vina、Python、RDKit、Meeko 已就绪"
                      : "需要检查 Vina、Python、RDKit、Meeko")}
                </dd>
              </div>
            </dl>
          </RightRailSection>

          <RightRailSection title={translate("修复顺序")}>
            <p>{translate("先重新检测随附 Vina 与 Assisted Python；仍不可用时再配置外部路径。RDKit / Meeko 缺失只影响自动准备。")}</p>
          </RightRailSection>
        </RightRail>
      </BodyGrid>
    </PageShell>
  );
}
