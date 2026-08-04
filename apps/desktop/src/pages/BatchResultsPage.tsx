import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { ArrowClockwise } from "@phosphor-icons/react";

import ActionButton from "../components/ActionButton";
import BatchScreeningPanel from "../components/BatchScreeningPanel";
import EmptyState from "../components/EmptyState";
import OperationLoadingDialog from "../components/OperationLoadingDialog";
import { PageHero, PageShell } from "../components/layout/PageLayout";
import type { DockStartProject } from "../types";

type BatchResultsPageProps = {
  project: DockStartProject;
  onBack: () => void;
  onOpenProjectHome: () => void;
};

type ScreeningProbe = {
  ok?: boolean;
  screening?: { status?: string } | null;
  error?: { message?: string; suggestion?: string } | null;
};

export default function BatchResultsPage({
  project,
  onBack,
  onOpenProjectHome,
}: BatchResultsPageProps) {
  const [probeState, setProbeState] = useState<"loading" | "available" | "empty" | "error">("loading");
  const [message, setMessage] = useState("");
  const requestRef = useRef(0);

  const probe = useCallback(async () => {
    const request = ++requestRef.current;
    setProbeState("loading");
    setMessage("");
    try {
      const parsed = JSON.parse(await invoke<string>("get_screening_status", {
        projectDir: project.project_dir,
      })) as ScreeningProbe;
      if (request !== requestRef.current) return;
      if (parsed.ok && parsed.screening) {
        setProbeState("available");
        return;
      }
      if (parsed.ok) {
        setProbeState("empty");
        return;
      }
      setProbeState("error");
      setMessage([
        parsed.error?.message || "无法读取批量筛选记录。",
        parsed.error?.suggestion,
      ].filter(Boolean).join(" "));
    } catch (error) {
      if (request !== requestRef.current) return;
      setProbeState("error");
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }, [project.project_dir]);

  useEffect(() => {
    void probe();
    return () => {
      requestRef.current += 1;
    };
  }, [probe]);

  if (probeState === "empty" || probeState === "error") {
    return (
      <section className="project-page batch-results-empty-page">
        <EmptyState
          title={probeState === "empty" ? "还没有批量筛选结果" : "批量筛选记录无法读取"}
          description={probeState === "empty"
            ? `项目 ${project.project_name} 尚未创建批量筛选任务。`
            : message}
          action={(
            <>
              <ActionButton variant="primary" onClick={onBack}>返回运行工作台</ActionButton>
              <ActionButton onClick={() => void probe()}><ArrowClockwise size={16} />重新读取</ActionButton>
              <ActionButton onClick={onOpenProjectHome}>回到项目总览</ActionButton>
            </>
          )}
        />
      </section>
    );
  }

  return (
    <PageShell labelledBy="batch-results-page-title" className="batch-results-page">
      <OperationLoadingDialog
        open={probeState === "loading"}
        title="正在读取批量结果"
        message="正在核对批量任务状态、评分汇总与构象文件。"
        detail="不会重新运行对接。"
      />
      <PageHero
        eyebrow="结果 · BATCH SCREENING"
        title="多配体批量筛选结果"
        titleId="batch-results-page-title"
        description="按配体查看完成状态、评分排名、最佳构象、汇总文件和历史归档。"
        actions={(
          <>
            <ActionButton onClick={() => void probe()} disabled={probeState === "loading"}>
              <ArrowClockwise size={16} />刷新结果
            </ActionButton>
            <ActionButton variant="text" onClick={onBack}>返回运行工作台</ActionButton>
          </>
        )}
      />
      {probeState === "available" ? (
        <BatchScreeningPanel
          projectDir={project.project_dir}
          receptorFile={project.receptor.file}
          box={project.box}
          vina={project.vina}
          presentation="results"
        />
      ) : null}
    </PageShell>
  );
}
