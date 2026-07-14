import {
  ArrowCounterClockwise,
  ArrowRight,
  BookOpenText,
  ChartBar,
  CheckCircle,
  Crosshair,
  Cube,
  Database,
  FileText,
  Flask,
  FolderOpen,
  Play,
  ShieldCheck,
  WarningCircle,
  Wrench,
} from "@phosphor-icons/react";
import ActionButton from "../components/ActionButton";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import StatusBadge from "../components/StatusBadge";
import { appVersion, type NavigateHandler, type PageId, type StartMode } from "../navigation/pages";
import type { DockStartProject } from "../types";

type HelpPageProps = {
  project: DockStartProject | null;
  onNavigate: NavigateHandler;
};

type StartRoute = {
  mode: StartMode;
  icon: typeof Database;
  eyebrow: string;
  title: string;
  description: string;
  requirement: string;
  action: string;
  tone: "ok" | "info" | "muted";
};

const startRoutes: StartRoute[] = [
  {
    mode: "basic",
    icon: Database,
    eyebrow: "BASIC STABLE",
    title: "已有 PDBQT（直接对接）",
    description: "直接导入受体与配体 PDBQT，设置搜索范围后运行 AutoDock Vina。",
    requirement: "随附 Vina · 不需要 RDKit / Meeko",
    action: "从 PDBQT 开始",
    tone: "ok",
  },
  {
    mode: "assisted",
    icon: Flask,
    eyebrow: "ASSISTED STABLE",
    title: "PDB/CIF + SDF/MOL（准备并转换）",
    description: "可在线搜索并下载，也可从电脑导入原始结构，再准备并转换为 PDBQT。",
    requirement: "随附 Python 3.11 · RDKit · Meeko",
    action: "选择结构来源",
    tone: "info",
  },
  {
    mode: "demo",
    icon: BookOpenText,
    eyebrow: "DEMO MODE",
    title: "示例项目（快速体验）",
    description: "复制内置示例，了解项目、搜索范围、运行记录和结果页面之间的关系。",
    requirement: "仅用于软件操作演示，不用于科研结论",
    action: "打开示例入口",
    tone: "muted",
  },
];

const workflowSteps: Array<{
  index: string;
  icon: typeof FolderOpen;
  title: string;
  description: string;
  page: PageId;
}> = [
  { index: "01", icon: FolderOpen, title: "获取或导入结构", description: "在线搜索 RCSB / PubChem，或从电脑导入支持的结构文件。", page: "structure-fetch" },
  { index: "02", icon: Wrench, title: "转换为 PDBQT", description: "将受体 PDB/CIF 与配体 SDF/MOL 准备为 Vina 输入；已有 PDBQT 可跳过。", page: "preparation" },
  { index: "03", icon: Cube, title: "设置搜索范围", description: "复核结构，定位 Box，并设置 Vina 参数。", page: "run-prepare" },
  { index: "04", icon: Play, title: "运行对接", description: "保存配置、创建运行记录并执行本地 Vina。", page: "run-prepare" },
  { index: "05", icon: ChartBar, title: "结果与报告", description: "查看构象和评分，导出 CSV 与 Markdown 实验记录。", page: "result" },
];

function projectTarget(project: DockStartProject | null, page: PageId): PageId {
  if (project || page === "project-create" || page === "toolchain-status") return page;
  return "project-create";
}

export default function HelpPage({ project, onNavigate }: HelpPageProps) {
  return (
    <section className="help-center-page" aria-labelledby="help-title">
      <header className="help-center-hero">
        <div className="help-center-hero-copy">
          <div className="help-center-kicker">
            <BookOpenText aria-hidden="true" size={16} />
            <span>DOCKSTART HELP CENTER</span>
            <StatusBadge tone="info">{`v${appVersion}`}</StatusBadge>
          </div>
          <h1 id="help-title">从结构文件到可复现对接记录</h1>
          <p>按你已有的文件选择入口；每一步都说明要做什么、为什么要做，以及遇到问题时去哪里排查。</p>
        </div>
        <div className="help-center-hero-actions">
          <ActionButton variant="secondary" onClick={() => onNavigate("toolchain-status")}>
            <Wrench aria-hidden="true" size={16} /> 检查工具链
          </ActionButton>
          <ActionButton variant="primary" onClick={() => onNavigate(project ? "home" : "project-create")}>
            {project ? "返回项目总览" : "创建第一个项目"} <ArrowRight aria-hidden="true" size={16} />
          </ActionButton>
        </div>
      </header>

      <div className="help-center-layout">
        <main className="help-center-main">
          <section className="help-center-section" aria-labelledby="help-start-title">
            <div className="help-section-heading">
              <div>
                <span>快速开始</span>
                <h2 id="help-start-title">你现在手里有什么文件？</h2>
              </div>
              <p>选择最接近当前情况的路径，不必先理解全部术语。</p>
            </div>
            <div className="help-route-grid">
              {startRoutes.map((route) => {
                const Icon = route.icon;
                return (
                  <article className={`help-route-card ${route.mode}`} key={route.mode}>
                    <div className="help-route-icon"><Icon aria-hidden="true" size={22} /></div>
                    <div className="help-route-copy">
                      <span>{route.eyebrow}</span>
                      <h3>{route.title}</h3>
                      <p>{route.description}</p>
                    </div>
                    <StatusBadge tone={route.tone}>{route.requirement}</StatusBadge>
                    <button type="button" onClick={() => onNavigate("project-create", { startMode: route.mode })}>
                      {route.action} <ArrowRight aria-hidden="true" size={15} />
                    </button>
                  </article>
                );
              })}
            </div>
          </section>

          <section className="help-center-section" aria-labelledby="help-workflow-title">
            <div className="help-section-heading">
              <div>
                <span>标准流程</span>
                <h2 id="help-workflow-title">一次完整对接包含五个阶段</h2>
              </div>
              <p>Basic 从第 1 阶段直接导入 PDBQT；Assisted 会经过第 2 阶段。</p>
            </div>
            <ol className="help-workflow">
              {workflowSteps.map((step) => {
                const Icon = step.icon;
                return (
                  <li key={step.index}>
                    <button type="button" onClick={() => onNavigate(projectTarget(project, step.page))}>
                      <span className="help-workflow-index">{step.index}</span>
                      <Icon aria-hidden="true" size={20} />
                      <strong>{step.title}</strong>
                      <small>{step.description}</small>
                    </button>
                  </li>
                );
              })}
            </ol>
          </section>

          <section className="help-center-section" aria-labelledby="help-box-title">
            <div className="help-section-heading">
              <div>
                <span>搜索范围</span>
                <h2 id="help-box-title">Box 定位与调整</h2>
              </div>
              <ActionButton
                variant="text"
                onClick={() => onNavigate(projectTarget(project, "run-prepare"))}
              >
                打开对接工作台 <ArrowRight aria-hidden="true" size={15} />
              </ActionButton>
            </div>
            <div className="help-feature-grid">
              <article>
                <Crosshair aria-hidden="true" size={21} />
                <div><strong>定位到受体</strong><p>读取受体 PDBQT 的原子坐标范围，把 Box 中心快速移动到该范围中心；不会改变尺寸。</p></div>
              </article>
              <article>
                <ArrowCounterClockwise aria-hidden="true" size={21} />
                <div><strong>重置参数</strong><p>恢复进入对接工作台时的 Box 中心和尺寸，适合撤销一轮大幅调整。</p></div>
              </article>
              <article>
                <Cube aria-hidden="true" size={21} />
                <div><strong>滚轮精调</strong><p>先绑定中心或尺寸参数，再用滚轮按 0.1、1 或 5 Å 调整；未绑定时滚轮仍用于缩放视图。</p></div>
              </article>
              <article>
                <WarningCircle aria-hidden="true" size={21} />
                <div><strong>不是口袋预测</strong><p>自动定位只是几何操作。结合位点、质子化、电荷、金属和缺失残基仍需人工判断。</p></div>
              </article>
            </div>
          </section>

          <section className="help-center-section" aria-labelledby="help-reliability-title">
            <div className="help-section-heading">
              <div>
                <span>稳定性与记录</span>
                <h2 id="help-reliability-title">最近版本带来的运行保障</h2>
              </div>
            </div>
            <div className="help-reliability-grid">
              <article><ShieldCheck aria-hidden="true" size={20} /><div><strong>工具检测缓存</strong><p>相同运行环境不会在每次切页时重复启动检测；需要时可手动重新检查。</p></div></article>
              <article><Play aria-hidden="true" size={20} /><div><strong>后台任务队列</strong><p>格式准备和 Vina 长任务在后台执行，界面只接收进度和结果事件。</p></div></article>
              <article><FileText aria-hidden="true" size={20} /><div><strong>可追踪产物</strong><p>配置、命令、stdout、stderr、日志、输入快照、SHA256 和报告随运行记录保存。</p></div></article>
              <article><ArrowCounterClockwise aria-hidden="true" size={20} /><div><strong>中断恢复</strong><p>异常退出后会保守识别未完成的准备或运行记录，并保留已有日志用于排查。</p></div></article>
            </div>
          </section>

          <section className="help-center-section help-troubleshooting" aria-labelledby="help-trouble-title">
            <div className="help-section-heading">
              <div>
                <span>故障排查</span>
                <h2 id="help-trouble-title">先按问题类型定位</h2>
              </div>
              <ActionButton variant="text" onClick={() => onNavigate("toolchain-status")}>打开安装后自检</ActionButton>
            </div>
            <div className="help-accordion">
              <details>
                <summary><span><Wrench aria-hidden="true" size={18} />工具或格式准备不可用</span><ArrowRight aria-hidden="true" size={15} /></summary>
                <p>先在工具链页重新检测。Basic 只需要 Vina 和已有 PDBQT；Assisted 才需要 Python、RDKit 与 Meeko。MOL2/SMILES 暂不支持自动准备。</p>
              </details>
              <details>
                <summary><span><Cube aria-hidden="true" size={18} />Box 看不到、太远或太大</span><ArrowRight aria-hidden="true" size={15} /></summary>
                <p>先点击“定位到受体”，再用“适应视图”复核。该按钮只移动中心；尺寸仍需根据已知位点和研究目的设置。</p>
              </details>
              <details>
                <summary><span><Play aria-hidden="true" size={18} />Vina 无法开始或运行中断</span><ArrowRight aria-hidden="true" size={15} /></summary>
                <p>运行前检查会集中列出阻塞项。若任务中断，请保留 run 目录，查看 stdout、stderr、log.txt 和 metadata.json，不要只重新覆盖运行。</p>
              </details>
              <details>
                <summary><span><ChartBar aria-hidden="true" size={18} />完成后没有评分或报告</span><ArrowRight aria-hidden="true" size={15} /></summary>
                <p>确认运行状态为 finished，并检查 out.pdbqt 与 log.txt 是否存在。完整流程会继续生成 scores.csv 和 Markdown 实验记录。</p>
              </details>
            </div>
          </section>

          <ScientificDisclaimer kind="score" />
        </main>

        <aside className="help-center-rail" aria-label="帮助页快捷信息">
          <section className="help-rail-project">
            <span>当前上下文</span>
            <h2>{project ? project.project_name : "尚未加载项目"}</h2>
            <p>{project ? "可以直接进入当前项目对应阶段。" : "创建或打开项目后，帮助页会保留项目快捷入口。"}</p>
            <StatusBadge tone={project ? "ok" : "muted"}>{project ? "项目已加载" : "等待项目"}</StatusBadge>
          </section>

          <section>
            <h2>快捷入口</h2>
            <nav className="help-rail-actions" aria-label="帮助快捷入口">
              <button type="button" onClick={() => onNavigate(project ? "home" : "project-create")}><FolderOpen aria-hidden="true" size={17} /><span>{project ? "项目总览" : "创建项目"}</span><ArrowRight aria-hidden="true" size={14} /></button>
              <button type="button" onClick={() => onNavigate(projectTarget(project, "preparation"))}><Wrench aria-hidden="true" size={17} /><span>格式转换</span><ArrowRight aria-hidden="true" size={14} /></button>
              <button type="button" onClick={() => onNavigate(projectTarget(project, "run-prepare"))}><Cube aria-hidden="true" size={17} /><span>对接工作台</span><ArrowRight aria-hidden="true" size={14} /></button>
              <button type="button" onClick={() => onNavigate(projectTarget(project, "result"))}><ChartBar aria-hidden="true" size={17} /><span>结果与报告</span><ArrowRight aria-hidden="true" size={14} /></button>
              <button type="button" onClick={() => onNavigate("toolchain-status")}><ShieldCheck aria-hidden="true" size={17} /><span>工具链与自检</span><ArrowRight aria-hidden="true" size={14} /></button>
            </nav>
          </section>

          <section>
            <h2>格式与输出</h2>
            <dl className="help-format-list">
              <div><dt>受体准备</dt><dd>PDB / CIF</dd></div>
              <div><dt>配体准备</dt><dd>SDF / MOL</dd></div>
              <div><dt>Vina 输入</dt><dd>PDBQT</dd></div>
              <div><dt>结果输出</dt><dd>PDBQT / CSV / MD</dd></div>
            </dl>
          </section>

          <section className="help-rail-boundary">
            <h2>当前边界</h2>
            <ul>
              <li><CheckCircle aria-hidden="true" size={15} />本地运行，不自动上传项目数据</li>
              <li><WarningCircle aria-hidden="true" size={15} />不支持 MOL2 / SMILES 自动准备</li>
              <li><WarningCircle aria-hidden="true" size={15} />不做口袋预测或药效判断</li>
            </ul>
          </section>
        </aside>
      </div>
    </section>
  );
}
