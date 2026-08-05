export type JsonCommandInvoke = (
  command: string,
  args?: Record<string, unknown>,
) => Promise<string>;

function ipcContractError(command: string, message: string): Error {
  return new Error(`IPC 契约错误：${command} ${message}`);
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validateBusinessFailure(
  payload: Record<string, unknown>,
  command: string,
): void {
  if (payload.ok !== false) return;

  if (!isJsonObject(payload.error)) {
    throw ipcContractError(
      command,
      "业务失败响应的 error 必须是非 null、非数组的 JSON 对象。",
    );
  }
  const error = payload.error;
  for (const field of ["code", "message"] as const) {
    if (typeof error[field] !== "string" || error[field].trim().length === 0) {
      throw ipcContractError(
        command,
        `业务失败响应的 error.${field} 必须是非空字符串。`,
      );
    }
  }
  for (const field of ["title", "raw_error", "suggestion"] as const) {
    if (
      Object.prototype.hasOwnProperty.call(error, field)
      && typeof error[field] !== "string"
    ) {
      throw ipcContractError(
        command,
        `业务失败响应的可选字段 error.${field} 必须是字符串。`,
      );
    }
  }
}

export function decodeIpcJsonResponse<T extends { ok: boolean }>(
  rawPayload: string,
  command: string,
): T {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawPayload);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw ipcContractError(command, `返回了无效 JSON：${detail}`);
  }

  if (!isJsonObject(parsed)) {
    throw ipcContractError(command, "返回值必须是 JSON 对象，不能是 null、数组或基础类型。");
  }
  if (typeof parsed.ok !== "boolean") {
    throw ipcContractError(command, "返回的数据缺少布尔型 ok 字段。");
  }
  validateBusinessFailure(parsed, command);
  return parsed as T;
}
