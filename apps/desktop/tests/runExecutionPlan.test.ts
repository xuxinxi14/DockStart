import assert from "node:assert/strict";
import test from "node:test";
import { metadataExecutionPlanStages } from "../src/utils/runExecutionPlan.ts";

test("local_only schema v2 展示输入评分与局部优化两阶段", () => {
  const stages = metadataExecutionPlanStages({
    execution_plan: {
      schema_version: 2,
      kind: "local_only_with_baseline",
      stages: [
        {
          id: "input_score",
          label: "输入姿势评分",
          command: ["vina", "--score_only"],
          log_file: "runs/run_001/baseline_log.txt",
          output_file: "",
        },
        {
          id: "local_optimization",
          label: "局部优化",
          command: ["vina", "--local_only", "--out", "runs/run_001/optimized.pdbqt"],
          log_file: "runs/run_001/log.txt",
          output_file: "runs/run_001/optimized.pdbqt",
        },
      ],
    },
  });

  assert.equal(stages.length, 2);
  assert.deepEqual(stages[0].command, ["vina", "--score_only"]);
  assert.equal(stages[1].outputFile, "runs/run_001/optimized.pdbqt");
});

test("未知或不完整执行计划不冒充当前双阶段协议", () => {
  assert.deepEqual(metadataExecutionPlanStages(null), []);
  assert.deepEqual(metadataExecutionPlanStages({
    execution_plan: {
      schema_version: 1,
      kind: "local_only_with_baseline",
      stages: [],
    },
  }), []);
});
