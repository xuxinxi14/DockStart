import assert from "node:assert/strict";
import test from "node:test";
import {
  effectiveProjectTaskIntent,
  projectCreateProtocol,
  projectTaskSwitch,
  taskIntentFromProject,
  taskIntentLabel,
  workflowRunForTask,
} from "../src/utils/vinaTask.ts";

test("新建普通项目默认使用全局对接且不启用自动范围", () => {
  assert.deepEqual(projectCreateProtocol("basic", undefined), {
    runMode: "dock",
    autobox: false,
    confirmPoseContext: false,
    workspaceMode: null,
  });
  assert.deepEqual(projectCreateProtocol("assisted", "dock"), {
    runMode: "dock",
    autobox: false,
    confirmPoseContext: false,
    workspaceMode: null,
  });
});

test("新建评价任务默认启用自动范围并限定为单配体", () => {
  assert.deepEqual(projectCreateProtocol("basic", "score_only"), {
    runMode: "score_only",
    autobox: true,
    confirmPoseContext: false,
    workspaceMode: "single",
  });
  assert.deepEqual(projectCreateProtocol("assisted", "local_only"), {
    runMode: "local_only",
    autobox: true,
    confirmPoseContext: false,
    workspaceMode: "single",
  });
});

test("示例项目忽略外部任务意图并使用示例自身设置", () => {
  assert.equal(effectiveProjectTaskIntent("demo", "score_only"), "dock");
  assert.equal(effectiveProjectTaskIntent("demo", "local_only"), "dock");
});

test("从全局对接切到评价任务时默认启用自动范围", () => {
  assert.deepEqual(projectTaskSwitch("dock", false, "score_only"), {
    runMode: "score_only",
    autobox: true,
    confirmPoseContext: false,
    workspaceMode: "single",
  });
});

test("姿势评分与局部优化之间切换时保留现有范围选择", () => {
  assert.equal(projectTaskSwitch("score_only", false, "local_only").autobox, false);
  assert.equal(projectTaskSwitch("local_only", true, "score_only").autobox, true);
});

test("切回全局对接时关闭自动范围且不强制改变工作区", () => {
  assert.deepEqual(projectTaskSwitch("local_only", true, "dock"), {
    runMode: "dock",
    autobox: false,
    confirmPoseContext: false,
    workspaceMode: null,
  });
});

test("旧项目和未知值安全回退为全局对接", () => {
  assert.equal(taskIntentFromProject(null), "dock");
  assert.equal(taskIntentFromProject({ docking_protocol: { run_mode: "unknown" } }), "dock");
  assert.equal(taskIntentFromProject({ docking_protocol: { run_mode: "local_only" } }), "local_only");
  assert.equal(taskIntentLabel("score_only"), "姿势评分");
  assert.equal(taskIntentLabel("local_only"), "局部优化");
});

test("当前任务优先恢复该模式最近运行而不是全局最新运行", () => {
  const workflow = {
    ok: true,
    project_dir: "demo",
    project: null,
    latest_run: {
      run_id: "run_003",
      run_mode: "score_only" as const,
      status: "finished",
    },
    latest_run_for_current_mode: {
      run_id: "run_001",
      run_mode: "dock" as const,
      status: "finished",
    },
  };
  assert.equal(workflowRunForTask(workflow, "dock")?.run_id, "run_001");
  assert.equal(workflowRunForTask(workflow, "score_only")?.run_id, "run_003");
  assert.equal(workflowRunForTask(workflow, "local_only"), null);
});

test("旧工作流响应缺少按模式字段时只接受模式匹配的全局最新运行", () => {
  const workflow = {
    ok: true,
    project_dir: "demo",
    project: null,
    latest_run: {
      run_id: "run_002",
      run_mode: "local_only" as const,
      status: "finished",
    },
  };
  assert.equal(workflowRunForTask(workflow, "local_only")?.run_id, "run_002");
  assert.equal(workflowRunForTask(workflow, "dock"), null);
});
