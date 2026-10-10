import { english } from "./en.ts";
import { getLanguage, type Language } from "./language.ts";

export function normalizeMessage(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

// Normalize both sides of lookup, and keep inherited object properties out of
// the message table (user-supplied text can be "constructor" or "__proto__").
const messages = new Map(Object.entries(english).map(([source, target]) =>
  [normalizeMessage(source), target]));

function interpolate(message: string, values: readonly unknown[]): string {
  return message.replace(/\{(\d+)\}/g, (placeholder, index: string) => {
    const position = Number(index);
    return position < values.length ? String(values[position] ?? "") : placeholder;
  });
}

type MessagePattern = { source: string; expression: RegExp; translation: string; positions: number[] };
let messagePatterns: MessagePattern[] | undefined;
const escapePattern = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");

function patterns(): MessagePattern[] {
  if (messagePatterns) return messagePatterns;
  messagePatterns = [];
  const entries = [...messages.entries()].sort(([left], [right]) =>
    right.replace(/\{\d+\}/g, "").length - left.replace(/\{\d+\}/g, "").length);
  for (const [source, translation] of entries) {
    if (!/\{\d+\}/.test(source) || !/[\u3400-\u9fff]/.test(source)) continue;
    const positions: number[] = [];
    let offset = 0;
    let pattern = "^";
    for (const match of source.matchAll(/\{(\d+)\}/g)) {
      pattern += escapePattern(source.slice(offset, match.index));
      pattern += "([\\s\\S]*?)";
      positions.push(Number(match[1]));
      offset = match.index! + match[0].length;
    }
    pattern += escapePattern(source.slice(offset)) + "$";
    messagePatterns.push({ source, expression: new RegExp(pattern), translation, positions });
  }
  return messagePatterns;
}

/** Translate at display boundaries; never mutate stored project data or logs. */
export function translate<T>(value: T, values?: readonly unknown[], locale: Language = getLanguage()): T {
  if (typeof value !== "string") return value;
  const source = normalizeMessage(value);
  const formatted = (message: string) => values ? interpolate(message, values) : message;
  const preserveSpacing = (message: string) =>
    (value.match(/^\s*/)?.[0] || message.match(/^\s*/)?.[0] || "") + message.trim()
    + (value.match(/\s*$/)?.[0] || message.match(/\s*$/)?.[0] || "");
  if (locale === "zh-CN") return formatted(value) as T;
  const translated = messages.get(source);
  if (translated !== undefined) return formatted(preserveSpacing(translated)) as T;
  // Backend review summaries join complete sentences. Resolve each known message
  // separately so a template cannot consume the remaining warnings as one value.
  if (!values && value.includes("。；")) {
    const parts = value.trim().split(/(?<=。)；/);
    const resolved = parts.map(part => translate(part, undefined, locale));
    if (parts.every((part, index) => !/[\u3400-\u9fff]/.test(part) || resolved[index] !== part)) {
      return preserveSpacing(resolved.join("; ")) as T;
    }
  }
  if (!values && /[\u3400-\u9fff]/.test(source)) {
    for (const pattern of patterns()) {
      const match = pattern.expression.exec(value.trim());
      if (!match) continue;
      const captures: unknown[] = [];
      pattern.positions.forEach((position, index) => { captures[position] = match[index + 1]; });
      // This placeholder is a fixed review subject (water, metal, altloc, etc.).
      // All other captures remain verbatim, including user names and file paths.
      if (pattern.source === "当前文件未包含足够化学信息；需要原始 PDB/mmCIF 才能检查{0}。") {
        captures[0] = translate(captures[0], undefined, locale);
      }
      return interpolate(preserveSpacing(pattern.translation), captures) as T;
    }
  }
  // Unmapped backend messages retain their exact original details.
  return formatted(value) as T;
}
