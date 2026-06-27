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
        eyebrow="HelpPage"
        title="DockStart 帮助与新手流程"
        description="这里解释 DockStart 的主要页面、文件类型和科学边界。V0.5.7 只新增前端帮助入口，不新增 docking、preparation 或 viewer 后端功能。"
        actions={
          <button className="primary-button" type="button" onClick={() => onNavigate(project ? "home" : "project-create")}>
            {project ? "回到项目总览" : "创建项目"}
          </button>
        }
      />

      <SectionCard title="推荐流程" description="适合第一次使用 DockStart 的顺序。">
        <OnboardingGuide onNavigate={onNavigate} />
      </SectionCard>

      <SectionCard title="文件类型怎么理解">
        <div className="help-grid">
          <article>
            <strong>raw 文件</strong>
            <p>从 RCSB/PubChem 下载或记录的 PDB/CIF/SDF/MOL 等原始文件。raw 文件不能直接运行 AutoDock Vina。</p>
          </article>
          <article>
            <strong>prepared PDBQT</strong>
            <p>`prepared/receptor.pdbqt` 和 `prepared/ligand.pdbqt` 才是 Vina 输入。可以自动准备，也可以手动导入。</p>
          </article>
          <article>
            <strong>configs/vina_config.txt</strong>
            <p>由项目内 prepared 文件、Box 参数和 Vina 参数生成。生成 config 不会执行 Vina。</p>
          </article>
          <article>
            <strong>runs/run_XXX/</strong>
            <p>每次 run 保存 metadata、命令、stdout、stderr、log、out 和后续 scores/report。</p>
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
            <p>先管理 raw，再生成或导入 prepared PDBQT。自动准备结果仍需人工检查。</p>
          </article>
          <article>
            <strong>3D 查看 / Box</strong>
            <p>显示结构、Box overlay 和 docking pose。它不是 pocket prediction 或相互作用分析工具。</p>
          </article>
          <article>
            <strong>Vina 运行 / 结果报告</strong>
            <p>按 config、prepare run、execute、parse、report 顺序走，便于保留可复现记录。</p>
          </article>
        </div>
      </SectionCard>

      <WarningCallout title="不要把 raw 文件当成 Vina 输入">
        <p>如果已经下载 raw receptor 或 raw ligand，但没有 prepared PDBQT，请先进入“准备 PDBQT”或手动导入 PDBQT。</p>
      </WarningCallout>

      <ScientificDisclaimer kind="preparation" />
      <ScientificDisclaimer kind="viewer" />
      <ScientificDisclaimer kind="score" />
    </section>
  );
}
