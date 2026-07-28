export type ExecutionPlanStage = {
  id: string;
  label: string;
  command: string[];
  stdoutFile: string;
  stderrFile: string;
  logFile: string;
  outputFile: string;
};

export function metadataExecutionPlanStages(
  metadata: Record<string, unknown> | null,
): ExecutionPlanStage[] {
  const plan = metadata?.execution_plan;
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) return [];
  const planRecord = plan as Record<string, unknown>;
  if (
    planRecord.kind !== "local_only_with_baseline"
    || planRecord.schema_version !== 2
    || !Array.isArray(planRecord.stages)
  ) return [];
  return planRecord.stages.flatMap((stage) => {
    if (!stage || typeof stage !== "object" || Array.isArray(stage)) return [];
    const record = stage as Record<string, unknown>;
    if (!Array.isArray(record.command) || record.command.length === 0) return [];
    return [{
      id: String(record.id || ""),
      label: String(record.label || record.id || "运行阶段"),
      command: record.command.map(String),
      stdoutFile: String(record.stdout_file || ""),
      stderrFile: String(record.stderr_file || ""),
      logFile: String(record.log_file || ""),
      outputFile: String(record.output_file || ""),
    }];
  });
}
