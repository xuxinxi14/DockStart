import assert from "node:assert/strict";
import test from "node:test";
import { decodeIpcJsonResponse } from "../src/api/core.ts";

test("IPC decoder preserves structured business failures", () => {
  const response = decodeIpcJsonResponse(
    '{"ok":false,"error":{"code":"MAPS_NOT_READY","message":"maps 未就绪"}}',
    "get_maps_status",
  );

  assert.deepEqual(response, {
    ok: false,
    error: { code: "MAPS_NOT_READY", message: "maps 未就绪" },
  });
});

test("IPC decoder reports invalid JSON as a Chinese contract error", () => {
  assert.throws(
    () => decodeIpcJsonResponse("{not-json", "load_status"),
    /IPC 契约错误：load_status 返回了无效 JSON/,
  );
});

test("IPC decoder rejects non-object JSON payloads", () => {
  for (const payload of ["null", "[]", '"text"', "42", "true"]) {
    assert.throws(
      () => decodeIpcJsonResponse(payload, "load_status"),
      /返回值必须是 JSON 对象/,
    );
  }
});

test("IPC decoder requires a boolean ok field", () => {
  for (const payload of ['{"message":"missing"}', '{"ok":"yes"}']) {
    assert.throws(
      () => decodeIpcJsonResponse(payload, "load_status"),
      /缺少布尔型 ok 字段/,
    );
  }
  assert.deepEqual(decodeIpcJsonResponse('{"ok":true}', "load_status"), {
    ok: true,
  });
});

test("IPC decoder requires a structured error for business failures", () => {
  for (const payload of [
    '{"ok":false}',
    '{"ok":false,"error":null}',
    '{"ok":false,"error":[]}',
  ]) {
    assert.throws(
      () => decodeIpcJsonResponse(payload, "load_status"),
      /error 必须是非 null、非数组的 JSON 对象/,
    );
  }

  for (const payload of [
    '{"ok":false,"error":{}}',
    '{"ok":false,"error":{"message":"失败"}}',
    '{"ok":false,"error":{"code":"   ","message":"失败"}}',
  ]) {
    assert.throws(
      () => decodeIpcJsonResponse(payload, "load_status"),
      /error\.code 必须是非空字符串/,
    );
  }

  for (const payload of [
    '{"ok":false,"error":{"code":"FAILED"}}',
    '{"ok":false,"error":{"code":"FAILED","message":"  "}}',
  ]) {
    assert.throws(
      () => decodeIpcJsonResponse(payload, "load_status"),
      /error\.message 必须是非空字符串/,
    );
  }
});

test("IPC decoder validates optional business error fields without requiring title", () => {
  assert.deepEqual(
    decodeIpcJsonResponse(
      '{"ok":false,"error":{"code":"FAILED","message":"失败","raw_error":"raw","suggestion":"重试"}}',
      "load_status",
    ),
    {
      ok: false,
      error: {
        code: "FAILED",
        message: "失败",
        raw_error: "raw",
        suggestion: "重试",
      },
    },
  );
  for (const field of ["title", "raw_error", "suggestion"]) {
    assert.throws(
      () => decodeIpcJsonResponse(
        JSON.stringify({
          ok: false,
          error: { code: "FAILED", message: "失败", [field]: null },
        }),
        "load_status",
      ),
      new RegExp(`error\\.${field} 必须是字符串`),
    );
  }
});
