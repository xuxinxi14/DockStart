import assert from "node:assert/strict";
import test from "node:test";
import {
  parseMarkdownBlocks,
  parseMarkdownInline,
  splitMarkdownTableRow,
} from "../src/utils/markdownPreview.ts";

test("Markdown preview parses the report block types without HTML execution", () => {
  const blocks = parseMarkdownBlocks([
    "# DockStart 报告",
    "",
    "正文包含 **重点** 与 `run_001`。",
    "",
    "- 第一项",
    "- 第二项",
    "",
    "| 项目 | 值 |",
    "| --- | ---: |",
    "| score | -7.2 |",
    "",
    "```text",
    "<script>alert('never')</script>",
    "```",
    "",
    "---",
  ].join("\n"));

  assert.deepEqual(blocks.map((block) => block.type), [
    "heading",
    "paragraph",
    "unordered-list",
    "table",
    "code",
    "rule",
  ]);
  assert.equal(blocks[4].type === "code" ? blocks[4].code : "", "<script>alert('never')</script>");
});

test("Markdown table parser preserves escaped pipes", () => {
  assert.deepEqual(splitMarkdownTableRow("| 路径 | a\\|b |"), ["路径", "a|b"]);
});

test("Inline parser recognizes emphasis and code as React-safe text tokens", () => {
  assert.deepEqual(parseMarkdownInline("**评分** 为 `-7.2`"), [
    { type: "strong", text: "评分" },
    { type: "text", text: " 为 " },
    { type: "code", text: "-7.2" },
  ]);
});
