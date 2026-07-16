import assert from "node:assert/strict";
import test from "node:test";
import { PageOperationScope } from "../src/utils/pageOperationScope.ts";
import { runRawToPreparedWorkflow } from "../src/utils/rawToPreparedWorkflow.ts";

test("disposed pages reject late payloads before parsing or committing", () => {
  const scope = new PageOperationScope();
  scope.activate();
  const token = scope.begin();
  let parsed = false;
  let committed = false;

  scope.dispose();

  const result = scope.parseIfCurrent(token, "{\"large\":true}", (payload) => {
    parsed = true;
    return JSON.parse(payload) as { large: boolean };
  });
  scope.commit(token, () => {
    committed = true;
  });

  assert.equal(result, undefined);
  assert.equal(parsed, false);
  assert.equal(committed, false);
  assert.equal(token.signal.aborted, true);
});

test("a replacement operation invalidates the previous operation", () => {
  const scope = new PageOperationScope();
  scope.activate();
  const previous = scope.begin();
  const current = scope.begin();

  assert.equal(previous.signal.aborted, true);
  assert.equal(scope.isCurrent(previous), false);
  assert.equal(scope.isCurrent(current), true);
  assert.equal(scope.finish(previous), false);
  assert.equal(scope.finish(current), true);
});

test("dispose clears ownership before abort listeners execute", () => {
  const scope = new PageOperationScope();
  scope.activate();
  const token = scope.begin();
  let wasCurrentDuringAbort = true;
  token.signal.addEventListener("abort", () => {
    wasCurrentDuringAbort = scope.isCurrent(token);
  });

  scope.dispose();

  assert.equal(wasCurrentDuringAbort, false);
});

test("the scope can be reactivated after a strict-mode cleanup cycle", () => {
  const scope = new PageOperationScope();
  scope.activate();
  const first = scope.begin();
  scope.dispose();
  scope.activate();
  const second = scope.begin();

  assert.equal(scope.isCurrent(first), false);
  assert.equal(scope.isCurrent(second), true);
});

test("raw-to-prepared workflow continues after the observing page is disposed", async () => {
  const scope = new PageOperationScope();
  scope.activate();
  const token = scope.begin();
  let prepared = false;
  let overwritePrepared = true;
  let lateUiCommit = false;

  const completed = await runRawToPreparedWorkflow({
    acquireRaw: async () => {
      scope.dispose();
      return true;
    },
    observerIsCurrent: () => scope.isCurrent(token),
    prepareRaw: async (overwrite) => {
      prepared = true;
      overwritePrepared = overwrite;
      scope.commit(token, () => {
        lateUiCommit = true;
      });
      return true;
    },
  });

  assert.equal(completed, true);
  assert.equal(prepared, true);
  assert.equal(overwritePrepared, false);
  assert.equal(lateUiCommit, false);
});

test("raw-to-prepared workflow retains overwrite permission while the page owns the token", async () => {
  const scope = new PageOperationScope();
  scope.activate();
  const token = scope.begin();
  let overwritePrepared = false;

  const completed = await runRawToPreparedWorkflow({
    acquireRaw: async () => true,
    observerIsCurrent: () => scope.isCurrent(token),
    prepareRaw: async (overwrite) => {
      overwritePrepared = overwrite;
      return true;
    },
  });

  assert.equal(completed, true);
  assert.equal(overwritePrepared, true);
});

test("raw-to-prepared workflow does not prepare after acquisition failure", async () => {
  let prepared = false;

  const completed = await runRawToPreparedWorkflow({
    acquireRaw: async () => false,
    observerIsCurrent: () => true,
    prepareRaw: async () => {
      prepared = true;
      return true;
    },
  });

  assert.equal(completed, false);
  assert.equal(prepared, false);
});
