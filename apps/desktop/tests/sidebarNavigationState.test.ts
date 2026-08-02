import assert from "node:assert/strict";
import test from "node:test";
import type { WorkflowStep } from "../src/components/WorkflowStepper.tsx";
import { resolveSidebarNavigationState } from "../src/utils/sidebarNavigationState.ts";

function workflowStep(targetPage: WorkflowStep["targetPage"], status: WorkflowStep["status"]): WorkflowStep {
  return {
    title: String(targetPage),
    description: "test",
    status,
    targetPage,
  };
}

test("运行工作台不会因首个 Box 子步骤完成而错误显示成功", () => {
  const steps = [
    workflowStep("run-prepare", "done"),
    workflowStep("run-prepare", "done"),
    workflowStep("run-prepare", "blocked"),
    workflowStep("run-execute", "blocked"),
  ];

  assert.equal(resolveSidebarNavigationState("run-prepare", true, true, steps), "blocked");
});

test("运行工作台只在全部配置和执行步骤完成后显示成功", () => {
  const steps = [
    workflowStep("run-prepare", "done"),
    workflowStep("run-prepare", "done"),
    workflowStep("run-execute", "done"),
  ];

  assert.equal(resolveSidebarNavigationState("run-prepare", true, true, steps), "ready");
});

test("受体转换失败时结构区和水合 AD4 均显示阻断状态", () => {
  const steps = [
    workflowStep("structure-fetch", "warning"),
    workflowStep("import-pdbqt", "available"),
    workflowStep("preparation", "failed"),
  ];

  assert.equal(resolveSidebarNavigationState("preparation", true, true, steps), "blocked");
  assert.equal(resolveSidebarNavigationState("hydrated-ad4", true, true, steps), "blocked");
});

test("水合 AD4 没有全局协议状态时不因项目存在而显示成功", () => {
  const steps = [
    workflowStep("structure-fetch", "done"),
    workflowStep("import-pdbqt", "done"),
    workflowStep("preparation", "done"),
  ];

  assert.equal(resolveSidebarNavigationState("hydrated-ad4", true, true, steps), "idle");
});

test("项目入口仍可由项目创建步骤显示成功", () => {
  const steps = [workflowStep("project-create", "done")];
  assert.equal(resolveSidebarNavigationState("home", true, false, steps), "ready");
});
