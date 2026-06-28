import OnboardingGuide from "../components/OnboardingGuide";
import PageHeader from "../components/PageHeader";
import ScientificDisclaimer from "../components/ScientificDisclaimer";
import SectionCard from "../components/SectionCard";
import WarningCallout from "../components/WarningCallout";
import type { PageId } from "../navigation/pages";
import type { DockStartProject } from "../types";

type HelpPageProps = {
  project: DockStartProject | null;
  onNavigate: (page: PageId) => void;
};

export default function HelpPage({ project, onNavigate }: HelpPageProps) {
  return (
    <section className="project-page">
      <PageHeader
        eyebrow="文档帮助"
        title="DockStart 帮助与新手流程"
        description="这里解释 DockStart 的主要页面、文件类型和科学边界。帮助页只做工作流说明，不新增 docking、准备或 viewer 后端功能。"
        actions={
          <button className="primary-button" type="button" onClick={() => onNavigate(project ? "home" : "project-create")}>
            {project ? "回到项目总览" : "创建项目"}
          </button>
        }
      />

      <SectionCard title="推荐流程" description="适合第一次使用 DockStart 的顺序。">
        <OnboardingGuide onNavigate={onNavigate} />
      </SectionCard>

      <SectionCard title="首次使用 DockStart">
        <div className="help-grid">
          <article>
            <strong>先看工具链</strong>
            <p>进入“工具链”页确认 AutoDock Vina、Python、RDKit 和 Meeko 状态。没有 Vina 时不能执行对接。</p>
          </article>
          <article>
            <strong>再配置路径</strong>
            <p>如果 Vina 或 Python 未配置，进入设置页配置 `vina.exe` 和独立 conda 环境中的 `python.exe`。</p>
          </article>
          <article>
            <strong>RDKit/Meeko 不会自动安装</strong>
            <p>DockStart 只检测和调用已存在的环境。推荐环境说明在 `docs/release/toolchain_environment.md`。</p>
          </article>
          <article>
            <strong>准备结果需要人工检查</strong>
            <p>自动 PDBQT preparation 不代表质子化、电荷、构象、金属或辅因子处理一定正确。</p>
          </article>
        </div>
      </SectionCard>

      <SectionCard title="文件类型怎么理解">
        <div className="help-grid">
          <article>
            <strong>原始结构文件</strong>
            <p>从 RCSB/PubChem 下载或记录的 PDB/CIF/SDF/MOL 等原始文件。原始结构文件不能直接运行 AutoDock Vina。</p>
          </article>
          <article>
            <strong>Vina 输入文件</strong>
            <p>`prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt` 才是 Vina 输入。可以自动准备，也可以手动导入。</p>
          </article>
          <article>
            <strong>configs/vina_config.txt</strong>
            <p>由项目内 Vina 输入文件、Box 参数和 Vina 参数生成。生成运行配置不会执行 Vina。</p>
          </article>
          <article>
            <strong>对接运行记录</strong>
            <p>每次运行保存命令、stdout、stderr、log、out 和后续 scores/report，便于复现和排查。</p>
          </article>
        </div>
      </SectionCard>

      <SectionCard title="页面速查">
        <div className="help-grid">
          <article>
            <strong>工具链</strong>
            <p>检查 Vina、Python、RDKit、Meeko 和内置资源。DockStart 不会自动安装 RDKit/Meeko。</p>
          </article>
          <article>
            <strong>获取结构 / 准备 PDBQT</strong>
            <p>先管理原始结构，再生成或导入 Vina 输入文件。自动准备结果仍需人工检查。</p>
          </article>
          <article>
            <strong>3D 查看 / Box</strong>
            <p>显示结构、搜索范围和对接构象。它不是 pocket prediction 或相互作用分析工具。</p>
          </article>
          <article>
            <strong>Vina 运行 / 结果报告</strong>
            <p>按运行配置、创建运行记录、执行、解析、导出实验记录顺序走，便于保留可复现记录。</p>
          </article>
        </div>
      </SectionCard>

      <WarningCallout title="不要把原始结构当成 Vina 输入">
        <p>如果已经下载原始受体或原始配体，但没有 PDBQT，请先进入“准备 Vina 输入”或手动导入 PDBQT。</p>
      </WarningCallout>

      <ScientificDisclaimer kind="preparation" />
      <ScientificDisclaimer kind="viewer" />
      <ScientificDisclaimer kind="score" />
    </section>
  );
}
