import { useEffect, useRef, type ReactNode } from "react";
import { X } from "@phosphor-icons/react";
import { appVersion } from "../navigation/pages";
import { topicUrl, type HelpTopic } from "../utils/helpContent";
import DocumentationLink from "./DocumentationLink";

export default function TopicHelpDialog({ topic, onClose, children }: { topic: HelpTopic; onClose: () => void; children?: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="topic-help-dialog" onCancel={onClose} onClose={onClose}
    onClick={event => { if (event.target === event.currentTarget) {
      const rect = event.currentTarget.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
    } }} aria-labelledby={`topic-help-${topic.id}`}>
    <header><div><small>操作说明 · v{appVersion} · 离线可读</small><h2 id={`topic-help-${topic.id}`}>{topic.title}</h2></div>
      <button type="button" className="secondary-button" aria-label="关闭说明" onClick={onClose}><X size={17} /></button></header>
    <div className="topic-help-body"><p>{topic.summary}</p><ol>{topic.steps.map(step => <li key={step}>{step}</li>)}</ol>
      <p className="help-skip-note"><strong>能否跳过？</strong>{topic.skip}</p></div>
    <footer>{children}<DocumentationLink url={topicUrl(topic)} /><button type="button" className="secondary-button" onClick={onClose}>关闭</button></footer>
  </dialog>;
}
