import { useState } from "react";
import { ArrowSquareOut, Copy } from "@phosphor-icons/react";
import { openExternalUrl } from "../utils/externalLink";

export default function DocumentationLink({ url, children = "阅读完整说明", className = "" }: {
  url: string; children?: React.ReactNode; className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [notice, setNotice] = useState("");
  async function open() {
    setBusy(true); setNotice("");
    try {
      const result = await openExternalUrl(url);
      setFailed(!result.ok);
      if (!result.ok) setNotice(result.message);
    } finally { setBusy(false); }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(url); setNotice("地址已复制。"); }
    catch { setNotice("请选中下面的地址，手动复制到浏览器。"); }
  }
  return <span className={`documentation-link ${className}`.trim()}>
    <button type="button" className="text-button inline" disabled={busy} onClick={() => void open()}>
      {busy ? "正在打开…" : children}<ArrowSquareOut size={14} aria-hidden="true" />
    </button>
    {failed ? <span className="documentation-link-fallback" role="status">
      <span>{notice}</span><input aria-label="文档地址，可手动复制" readOnly value={url} onFocus={event => event.target.select()} />
      <button type="button" className="text-button inline" onClick={() => void copy()}><Copy size={14} aria-hidden="true" />复制地址</button>
    </span> : null}
  </span>;
}
