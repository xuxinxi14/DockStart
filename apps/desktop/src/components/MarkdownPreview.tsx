import { createElement, useMemo, type ReactNode } from "react";
import { Eye, LockSimple } from "@phosphor-icons/react";
import {
  parseMarkdownBlocks,
  parseMarkdownInline,
  type MarkdownAlignment,
} from "../utils/markdownPreview";

type MarkdownPreviewProps = {
  content: string;
  path: string;
  loading?: boolean;
  error?: string;
};

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  return parseMarkdownInline(text).map((token, index) => {
    const key = `${keyPrefix}-${index}`;
    if (token.type === "strong") return <strong key={key}>{token.text}</strong>;
    if (token.type === "emphasis") return <em key={key}>{token.text}</em>;
    if (token.type === "code") return <code key={key}>{token.text}</code>;
    return <span key={key}>{token.text}</span>;
  });
}

function alignmentClass(alignment: MarkdownAlignment): string | undefined {
  return alignment ? `is-${alignment}` : undefined;
}

export default function MarkdownPreview({ content, path, loading = false, error = "" }: MarkdownPreviewProps) {
  const blocks = useMemo(() => parseMarkdownBlocks(content), [content]);
  const pathParts = path.split(/[\\/]/).filter(Boolean);
  const filename = pathParts[pathParts.length - 1] || "Markdown 报告";

  return (
    <section className="markdown-reading-view" aria-label="Markdown 报告只读预览" aria-busy={loading}>
      <header className="markdown-reading-toolbar">
        <span className="markdown-reading-icon"><Eye aria-hidden="true" size={18} /></span>
        <div>
          <span>报告阅读视图</span>
          <strong>{filename}</strong>
          <small>{path || "尚未生成报告文件"}</small>
        </div>
        <span className="markdown-reading-lock"><LockSimple aria-hidden="true" size={13} /> 只读</span>
      </header>

      {loading ? (
        <div className="markdown-reading-state">正在读取报告并核对项目路径…</div>
      ) : error ? (
        <div className="markdown-reading-state is-error">
          <strong>无法显示报告预览</strong>
          <p>{error}</p>
        </div>
      ) : !content.trim() ? (
        <div className="markdown-reading-state">
          <strong>尚无可预览的 Markdown 报告</strong>
          <p>生成报告后，这里会以只读阅读视图显示实际保存的文件内容。</p>
        </div>
      ) : (
        <article className="markdown-reading-document">
          {blocks.map((block, blockIndex) => {
            const key = `${block.type}-${blockIndex}`;
            if (block.type === "heading") {
              const level = Math.max(1, Math.min(6, block.level));
              return createElement(
                `h${level}`,
                { key },
                renderInline(block.text, key),
              );
            }
            if (block.type === "paragraph") {
              return <p key={key}>{renderInline(block.text, key)}</p>;
            }
            if (block.type === "blockquote") {
              return <blockquote key={key}>{renderInline(block.text, key)}</blockquote>;
            }
            if (block.type === "rule") return <hr key={key} />;
            if (block.type === "code") {
              return (
                <pre key={key} data-language={block.language}>
                  <code>{block.code}</code>
                </pre>
              );
            }
            if (block.type === "unordered-list" || block.type === "ordered-list") {
              const List = block.type === "ordered-list" ? "ol" : "ul";
              return (
                <List key={key}>
                  {block.items.map((item, itemIndex) => (
                    <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
                  ))}
                </List>
              );
            }
            if (block.type !== "table") return null;
            return (
              <div className="markdown-reading-table" key={key}>
                <table>
                  <thead>
                    <tr>
                      {block.headers.map((header, cellIndex) => (
                        <th className={alignmentClass(block.alignments[cellIndex] ?? null)} key={`${key}-head-${cellIndex}`}>
                          {renderInline(header, `${key}-head-${cellIndex}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, rowIndex) => (
                      <tr key={`${key}-row-${rowIndex}`}>
                        {block.headers.map((_, cellIndex) => (
                          <td className={alignmentClass(block.alignments[cellIndex] ?? null)} key={`${key}-row-${rowIndex}-${cellIndex}`}>
                            {renderInline(row[cellIndex] ?? "", `${key}-row-${rowIndex}-${cellIndex}`)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
        </article>
      )}
    </section>
  );
}
