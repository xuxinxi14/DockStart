export type MarkdownAlignment = "left" | "center" | "right" | null;

export type MarkdownBlock =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "unordered-list" | "ordered-list"; items: string[] }
  | { type: "blockquote"; text: string }
  | { type: "code"; language: string; code: string }
  | { type: "table"; headers: string[]; alignments: MarkdownAlignment[]; rows: string[][] }
  | { type: "rule" };

export type MarkdownInlineToken = {
  type: "text" | "strong" | "emphasis" | "code";
  text: string;
};

const HEADING_PATTERN = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
const UNORDERED_LIST_PATTERN = /^\s*[-+*]\s+(.+)$/;
const ORDERED_LIST_PATTERN = /^\s*\d+[.)]\s+(.+)$/;
const BLOCKQUOTE_PATTERN = /^\s*>\s?(.*)$/;
const FENCE_PATTERN = /^\s*```\s*([^\s`]*)\s*$/;
const RULE_PATTERN = /^\s{0,3}(?:(?:-\s*){3,}|(?:_\s*){3,}|(?:\*\s*){3,})$/;

function isTableSeparatorCell(value: string): boolean {
  return /^:?-{3,}:?$/.test(value.trim());
}

function tableAlignment(value: string): MarkdownAlignment {
  const trimmed = value.trim();
  if (trimmed.startsWith(":") && trimmed.endsWith(":")) return "center";
  if (trimmed.endsWith(":")) return "right";
  if (trimmed.startsWith(":")) return "left";
  return null;
}

export function splitMarkdownTableRow(row: string): string[] {
  let source = row.trim();
  if (source.startsWith("|")) source = source.slice(1);
  if (source.endsWith("|") && !source.endsWith("\\|")) source = source.slice(0, -1);

  const cells: string[] = [];
  let current = "";
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === "\\" && source[index + 1] === "|") {
      current += "|";
      index += 1;
      continue;
    }
    if (character === "|") {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += character;
  }
  cells.push(current.trim());
  return cells;
}

function isTableStart(lines: string[], index: number): boolean {
  if (index + 1 >= lines.length || !lines[index].includes("|")) return false;
  const separators = splitMarkdownTableRow(lines[index + 1]);
  return separators.length > 0 && separators.every(isTableSeparatorCell);
}

function isBlockStart(lines: string[], index: number): boolean {
  const line = lines[index] ?? "";
  return Boolean(
    FENCE_PATTERN.test(line)
      || HEADING_PATTERN.test(line)
      || UNORDERED_LIST_PATTERN.test(line)
      || ORDERED_LIST_PATTERN.test(line)
      || BLOCKQUOTE_PATTERN.test(line)
      || RULE_PATTERN.test(line)
      || isTableStart(lines, index),
  );
}

export function parseMarkdownBlocks(markdown: string): MarkdownBlock[] {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(FENCE_PATTERN);
    if (fence) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !FENCE_PATTERN.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push({ type: "code", language: fence[1] || "text", code: codeLines.join("\n") });
      continue;
    }

    const heading = line.match(HEADING_PATTERN);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
      continue;
    }

    if (RULE_PATTERN.test(line)) {
      blocks.push({ type: "rule" });
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const headers = splitMarkdownTableRow(line);
      const separator = splitMarkdownTableRow(lines[index + 1]);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
        rows.push(splitMarkdownTableRow(lines[index]));
        index += 1;
      }
      blocks.push({
        type: "table",
        headers,
        alignments: separator.map(tableAlignment),
        rows,
      });
      continue;
    }

    const unordered = line.match(UNORDERED_LIST_PATTERN);
    if (unordered) {
      const items: string[] = [];
      while (index < lines.length) {
        const item = lines[index].match(UNORDERED_LIST_PATTERN);
        if (!item) break;
        items.push(item[1]);
        index += 1;
      }
      blocks.push({ type: "unordered-list", items });
      continue;
    }

    const ordered = line.match(ORDERED_LIST_PATTERN);
    if (ordered) {
      const items: string[] = [];
      while (index < lines.length) {
        const item = lines[index].match(ORDERED_LIST_PATTERN);
        if (!item) break;
        items.push(item[1]);
        index += 1;
      }
      blocks.push({ type: "ordered-list", items });
      continue;
    }

    const quote = line.match(BLOCKQUOTE_PATTERN);
    if (quote) {
      const quoteLines: string[] = [];
      while (index < lines.length) {
        const quoted = lines[index].match(BLOCKQUOTE_PATTERN);
        if (!quoted) break;
        quoteLines.push(quoted[1]);
        index += 1;
      }
      blocks.push({ type: "blockquote", text: quoteLines.join("\n") });
      continue;
    }

    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    if (paragraph.length) {
      blocks.push({ type: "paragraph", text: paragraph.join(" ") });
    } else {
      // Defensive progress for unusual Markdown that does not match a supported block.
      blocks.push({ type: "paragraph", text: line.trim() });
      index += 1;
    }
  }

  return blocks;
}

export function parseMarkdownInline(text: string): MarkdownInlineToken[] {
  const tokens: MarkdownInlineToken[] = [];
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_)/g;
  let cursor = 0;

  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > cursor) tokens.push({ type: "text", text: text.slice(cursor, start) });
    const value = match[0];
    if (value.startsWith("`")) {
      tokens.push({ type: "code", text: value.slice(1, -1) });
    } else if (value.startsWith("**") || value.startsWith("__")) {
      tokens.push({ type: "strong", text: value.slice(2, -2) });
    } else {
      tokens.push({ type: "emphasis", text: value.slice(1, -1) });
    }
    cursor = start + value.length;
  }

  if (cursor < text.length) tokens.push({ type: "text", text: text.slice(cursor) });
  return tokens.length ? tokens : [{ type: "text", text }];
}
