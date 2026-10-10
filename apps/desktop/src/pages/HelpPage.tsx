import { translate } from "../i18n/translate";
import { useLanguage } from "../i18n/useLanguage";
import { useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { ArrowRight, ArrowClockwise, BookOpenText, FolderOpen, MagnifyingGlass, ShieldCheck, Wrench } from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import DocumentationLink from "../components/DocumentationLink";
import StatusBadge from "../components/StatusBadge";
import TopicHelpDialog from "../components/TopicHelpDialog";
import { appVersion, type NavigateHandler } from "../navigation/pages";
import type { AppCapabilityProfile, DockStartProject, ProjectWorkflowStatusResponse } from "../types";
import { DOCUMENTATION_URL, helpTopics, helpTopicForError, searchDocumentationUrl, searchHelpTopics, type HelpTopic } from "../utils/helpContent";
import { recommendHelp } from "../utils/helpRecommendation";
import { isSameProjectDir } from "../utils/backgroundProjectRefresh";

type HelpPageProps = { project: DockStartProject | null; onNavigate: NavigateHandler };
type HelpTab = "all" | "入门" | "操作" | "排错" | "进阶";

export default function HelpPage({ project, onNavigate }: HelpPageProps) {
  useLanguage();
  const [profile, setProfile] = useState<AppCapabilityProfile | null>(null);
  const [workflow, setWorkflow] = useState<ProjectWorkflowStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [statusError, setStatusError] = useState("");
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<HelpTab>("all");
  const [showAll, setShowAll] = useState(false);
  const [selectedTopic, setSelectedTopic] = useState<HelpTopic | null>(null);
  const [online, setOnline] = useState(navigator.onLine);

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update); window.addEventListener("offline", update);
    return () => { window.removeEventListener("online", update); window.removeEventListener("offline", update); };
  }, []);
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setStatusError(""); setWorkflow(null);
    const projectDir = project?.project_dir;
    async function load() {
      const requests = await Promise.allSettled([
        invoke<string>("get_app_capability_profile"),
        projectDir ? invoke<string>("get_project_workflow_status", { projectDir }) : Promise.resolve("null"),
      ]);
      if (cancelled) return;
      const notices: string[] = [];
      try {
        if (requests[0].status !== "fulfilled") throw new Error("capability");
        const parsed = JSON.parse(requests[0].value) as AppCapabilityProfile;
        if (!parsed?.ok) throw new Error("capability");
        setProfile(parsed);
      } catch { setProfile(null); notices.push("工具状态读取失败，可进入工具链页重新检测。"); }
      try {
        if (requests[1].status !== "fulfilled") throw new Error("workflow");
        const parsed = JSON.parse(requests[1].value) as ProjectWorkflowStatusResponse | null;
        if (projectDir && (!parsed?.ok || !isSameProjectDir(parsed.project_dir, projectDir))) throw new Error("workflow");
        setWorkflow(parsed);
      } catch { notices.push("项目状态读取失败，可打开项目页查看或刷新。"); }
      setStatusError(notices.join(" ")); setLoading(false);
    }
    void load();
    return () => { cancelled = true; };
  }, [project?.project_dir, project?.updated_at, refresh]);

  const recommendation = recommendHelp(project, workflow, profile);
  const recommendedTopic = helpTopics.find(topic => topic.id === recommendation.topic)!;
  const errorCode = query.trim().toUpperCase().match(/^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$/)?.[0];
  const errorTopic = errorCode ? helpTopicForError(errorCode) : null;
  const topics = useMemo(() => {
    const matches = searchHelpTopics(query);
    if (errorTopic && !matches.some(topic => topic.id === errorTopic.id)) matches.unshift(errorTopic);
    return matches.filter(topic => tab === "all" || topic.group === tab);
  }, [query, tab, errorTopic]);
  const faqs = ["profiles", "structure", "tasks", "maps", "results", "errors"];
  function openAction(topic: HelpTopic) {
    if (!topic.page) return;
    if (topic.page === "project-create") onNavigate("project-create", { startMode: "demo" });
    else onNavigate(!project && !["toolchain-status", "settings", "home"].includes(topic.page) ? "project-create" : topic.page);
  }

  return <section className="help-hub" aria-labelledby="help-title">
    <header className="help-hub-header">
      <div><div className="help-hub-kicker"><BookOpenText size={16} aria-hidden="true" /><span>{translate("帮助与入门")}</span><StatusBadge tone="info">{translate(`v${appVersion}`)}</StatusBadge></div>
        <h1 id="help-title">{translate("下一步怎么做？")}</h1><p>{translate("继续当前项目，或查阅对应的操作、原理与排错说明。")}</p></div>
      <DocumentationLink url={DOCUMENTATION_URL}>{translate("打开完整文档")}</DocumentationLink>
    </header>

    <section className="help-next" aria-labelledby="help-next-title" aria-busy={loading}>
      <div><small>{project ? project.project_name : translate("尚未打开项目")}{translate(" · 当前建议")}</small>
        <h2 id="help-next-title">{translate(loading ? "正在读取当前状态…" : recommendation.title)}</h2>
        <p>{translate(loading ? "离线帮助与文档入口仍可使用。" : recommendation.description)}</p>
        {statusError ? <p role="status">{translate(statusError)}</p> : null}</div>
      <div className="help-button-row">
        <ActionButton variant="primary" disabled={loading} onClick={() => onNavigate(recommendation.page, { runId: recommendation.runId })}>{translate(recommendation.action)}<ArrowRight size={16} aria-hidden="true" /></ActionButton>
        <ActionButton variant="text" onClick={() => setSelectedTopic(recommendedTopic)}>{translate("操作说明")}</ActionButton>
        <ActionButton variant="text" disabled={loading} aria-label={translate("刷新工具与项目状态")} onClick={() => setRefresh(value => value + 1)}><ArrowClockwise size={16} aria-hidden="true" />{translate("刷新")}</ActionButton>
      </div>
    </section>

    <section className="help-start-row" aria-label={translate("快速开始")}>
      <article><FolderOpen size={21} aria-hidden="true" /><div><h2>{translate(project ? "继续使用当前结构" : "使用自己的结构")}</h2><p>{translate("受体 PDBQT/PDB/CIF；配体 PDBQT/SDF/MOL/单分子 MOL2。")}</p>
        <ActionButton variant="text" onClick={() => onNavigate(project ? "preparation" : "project-create", { startMode: profile?.assisted_mode_available ? "assisted" : "basic" })}>{translate(project ? "查看结构准备" : "创建项目并选择文件")}<ArrowRight size={14} aria-hidden="true" /></ActionButton></div></article>
      <article><BookOpenText size={21} aria-hidden="true" /><div><h2>{translate("体验内置示例")}</h2><p>{translate(profile?.ok && !profile.demo_mode_available ? "当前未检测到示例资源，请检查安装目录。" : "先熟悉流程，或直接打开已有结果。示例仅用于教学。")}</p>
        <ActionButton variant="text" disabled={profile?.ok === true && !profile.demo_mode_available} onClick={() => onNavigate("project-create", { startMode: "demo" })}>{translate("打开示例入口")}<ArrowRight size={14} aria-hidden="true" /></ActionButton></div></article>
    </section>

    <div className="help-hub-layout">
      <section className="help-topics-panel" aria-labelledby="help-search-title">
        <header><h2 id="help-search-title">{translate("查找帮助")}</h2><small>{translate("本地说明离线可读")}</small></header>
        <form className="help-search" onSubmit={event => { event.preventDefault(); setTab("all"); }}>
          <label className="help-search-input"><MagnifyingGlass size={18} aria-hidden="true" /><input type="search" aria-label={translate("搜索本地帮助主题或错误码")} placeholder={translate("搜索 Box、PDBQT、RMSD 或错误码…")} maxLength={160} value={query} onChange={event => setQuery(event.target.value)} /></label>
          <ActionButton type="submit">{translate("查找")}</ActionButton>
        </form>
        <div className="help-online-search"><DocumentationLink url={searchDocumentationUrl(query)}>{translate("在在线文档中搜索")}{query.trim() ? translate("此关键词") : ""}</DocumentationLink><small>{translate("仅在点击时打开浏览器；关键词不会自动发送。")}</small></div>
        <div className="help-topic-filters" role="group" aria-label={translate("帮助主题分类")}>
          {(["all", "入门", "操作", "排错", "进阶"] as HelpTab[]).map(value => <button type="button" key={value} aria-pressed={tab === value} className={tab === value ? "is-active" : ""} onClick={() => setTab(value)}>{translate(value === "all" ? "全部" : value)}</button>)}
        </div>
        {errorTopic ? <div className="help-code-result" role="status"><code>{errorCode}</code><p>{translate("按错误类型查看“")}{translate(errorTopic.title)}{translate("”。这是排查方向，具体原因仍需结合本次日志确认。")}</p><ActionButton variant="text" onClick={() => setSelectedTopic(errorTopic)}>{translate("查看排查步骤")}</ActionButton></div> : null}
        <div className="help-topic-grid">
          {(tab === "all" && !query.trim() && !showAll ? topics.slice(0, 8) : topics).map(topic => <button className="help-topic-card" type="button" key={topic.id} onClick={() => setSelectedTopic(topic)}>
            <span className="help-topic-category">{translate(topic.group)}</span><strong>{translate(topic.title)}</strong><span>{translate(topic.summary)}</span><small>{translate("查看说明")}<ArrowRight size={14} aria-hidden="true" /></small>
          </button>)}
        </div>
        {tab === "all" && !query.trim() ? <ActionButton variant="text" onClick={() => setShowAll(value => !value)}>{translate(showAll ? "收起进阶与排错主题" : `查看全部 ${helpTopics.length} 个主题`)}</ActionButton> : null}
        {!topics.length ? <p className="help-empty" role="status">{translate("没有匹配的本地主题。试试更短的关键词，或点击在线文档搜索。")}</p> : null}
        <footer className="help-documentation-note"><p>{translate("软件内说明适用于 v")}{translate(appVersion)}{translate("。在线文档独立更新，部分正文依据 v0.14.3 整理，案例可能使用更新界面；以章节适用版本和当前软件的支持范围为准。")}</p>
          {!online ? <p role="status">{translate("当前系统报告离线；本地操作说明和 FAQ 仍可阅读，在线文档需要网络。")}</p> : null}</footer>
      </section>

      <aside className="help-support" aria-label={translate("排错与快捷入口")}>
        <section><h2>{translate("遇到问题？")}</h2><p>{translate("保留项目与日志，先看中文提示和错误码，再处理对应的阻塞项。")}</p>
          <div className="help-support-actions"><ActionButton onClick={() => onNavigate("toolchain-status")}><Wrench size={16} aria-hidden="true" />{translate("工具链检查")}</ActionButton>
          <ActionButton onClick={() => onNavigate("toolchain-status")}><ShieldCheck size={16} aria-hidden="true" />{translate("安装自检与诊断导出")}</ActionButton>
          <DocumentationLink url="https://github.com/xuxinxi14/DockStart/issues">{translate("反馈问题")}</DocumentationLink></div>
          <small>{translate("反馈请附软件版本、安装档位、错误码和复现步骤；分享日志前检查本机路径和研究数据。")}</small></section>
        <section><h2>{translate("离线常见问题")}</h2><div className="help-local-faq">{faqs.map(id => {
          const topic = helpTopics.find(item => item.id === id)!;
          return <details key={id}><summary>{translate(topic.title)}</summary><p>{translate(topic.summary)}</p><p>{translate(topic.skip)}</p>
            <ActionButton variant="text" onClick={() => setSelectedTopic(topic)}>{translate("查看操作说明")}</ActionButton></details>;
        })}</div></section>
        <section><h2>{translate("项目快捷入口")}</h2><div className="help-support-actions">
          <ActionButton variant="text" onClick={() => onNavigate(project ? "home" : "project-create")}>{translate(project ? "项目总览" : "创建项目")}</ActionButton>
          <ActionButton variant="text" onClick={() => onNavigate("project-create")}>{translate("新建另一个项目")}</ActionButton>
          <ActionButton variant="text" onClick={() => onNavigate("settings")}>{translate("启动页面与设置")}</ActionButton>
        </div></section>
        <section className="help-scientific-note"><strong>{translate("理解结果的边界")}</strong><p>{translate("Docking score 仅供结构结合趋势参考，不能替代实验验证。文件检查通过或成功生成 PDBQT，不代表结构方案在科学上正确。")}</p></section>
      </aside>
    </div>
    {selectedTopic ? <TopicHelpDialog topic={selectedTopic} onClose={() => setSelectedTopic(null)}>
      {selectedTopic.page ? <ActionButton onClick={() => { openAction(selectedTopic); setSelectedTopic(null); }}>{translate("前往相关操作")}</ActionButton> : null}
    </TopicHelpDialog> : null}
  </section>;
}
